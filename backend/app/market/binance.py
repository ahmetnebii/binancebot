"""Binance USDⓈ-M canlı piyasa verisi: REST (başlangıç/boşluk doldurma) + WebSocket akışları.

2026 yapısı: WebSocket adresleri /public (bookTicker, depth), /market (kline, markPrice,
ticker...) ve /private (kullanıcı verisi) olarak ayrılmıştır. Bu modül /market yolunu kullanır.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable, Optional

import aiohttp

from ..events import EventLog
from ..models import Candle, SymbolInfo
from ..settings import ScannerSettings
from ..util import now_ms
from .base import CandleBuffer, MarketData, MarkInfo, Ticker
from .universe import select_universe

log = logging.getLogger(__name__)

STREAMS_PER_CONN = 200
SUB_CHUNK = 50
WEIGHT_BUDGET = 1500  # dakikalık 2400 limitinin altında güvenli bütçe
ROTATE_MS = 23 * 3600 * 1000


def parse_exchange_info(data: dict) -> dict[str, SymbolInfo]:
    out: dict[str, SymbolInfo] = {}
    for s in data.get("symbols", []):
        f = {x.get("filterType"): x for x in s.get("filters", [])}
        pf = f.get("PRICE_FILTER", {})
        ls = f.get("LOT_SIZE", {})
        mls = f.get("MARKET_LOT_SIZE", {})
        mn = f.get("MIN_NOTIONAL", {})
        out[s["symbol"]] = SymbolInfo(
            symbol=s["symbol"],
            base=s.get("baseAsset", ""),
            quote=s.get("quoteAsset", ""),
            tick_size=str(pf.get("tickSize", "0.01")),
            step_size=str(ls.get("stepSize", "0.001")),
            market_step_size=str(mls.get("stepSize", ls.get("stepSize", "0.001"))),
            min_qty=str(ls.get("minQty", "0")),
            max_qty=str(ls.get("maxQty", "1000000000")),
            market_max_qty=str(mls.get("maxQty", ls.get("maxQty", "1000000000"))),
            min_notional=str(mn.get("notional", "5")),
            onboard_date=int(s.get("onboardDate", 0) or 0),
            delivery_date=int(s.get("deliveryDate", 0) or 0),
            status=str(s.get("status", "")),
            contract_type=str(s.get("contractType", "")),
        )
    return out


def parse_klines(rows: list, now: int) -> list[Candle]:
    out: list[Candle] = []
    for r in rows:
        out.append(
            Candle(
                t=int(r[0]),
                o=float(r[1]),
                h=float(r[2]),
                l=float(r[3]),
                c=float(r[4]),
                v=float(r[5]),
                qv=float(r[7]),
                tbq=float(r[10]),
                closed=int(r[6]) < now,
            )
        )
    return out


def parse_ticker_24h(rows: list) -> dict[str, Ticker]:
    out: dict[str, Ticker] = {}
    for t in rows:
        try:
            last = float(t["lastPrice"])
            op = float(t.get("openPrice", last) or last)
            out[t["symbol"]] = Ticker(
                last=last,
                open=op,
                high=float(t.get("highPrice", last)),
                low=float(t.get("lowPrice", last)),
                quote_volume=float(t.get("quoteVolume", 0.0)),
                change_pct=float(t.get("priceChangePercent", 0.0)),
                ts=int(t.get("closeTime", 0) or 0),
            )
        except (KeyError, ValueError, TypeError):
            continue
    return out


class _StreamConn:
    """Tek bir birleşik akış (combined stream) bağlantısı."""

    def __init__(self, md: "BinanceMarketData", name: str, streams: list[str]):
        self.md = md
        self.name = name
        self.streams: list[str] = list(dict.fromkeys(streams))
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.task: Optional[asyncio.Task] = None
        self._id = 0
        self.connected = False

    def start(self) -> None:
        self.task = asyncio.create_task(self._run(), name=f"ws-{self.name}")

    async def _send(self, method: str, params: list[str]) -> None:
        if self.ws is None or self.ws.closed:
            return
        for i in range(0, len(params), SUB_CHUNK):
            self._id += 1
            await self.ws.send_str(json.dumps({"method": method, "params": params[i : i + SUB_CHUNK], "id": self._id}))
            await asyncio.sleep(0.3)  # bağlantı başına saniyede ≤ 10 mesaj kuralı

    async def add(self, streams: list[str]) -> None:
        new = [s for s in streams if s not in self.streams]
        if not new:
            return
        self.streams.extend(new)
        await self._send("SUBSCRIBE", new)

    async def remove(self, streams: list[str]) -> None:
        gone = [s for s in streams if s in self.streams]
        if not gone:
            return
        self.streams = [s for s in self.streams if s not in gone]
        await self._send("UNSUBSCRIBE", gone)

    async def _run(self) -> None:
        backoff = 1.0
        md = self.md
        while not md._stopping:
            initial = self.streams[:20]
            rest = self.streams[20:]
            url = f"{md.ws_base}{md.market_path}?streams={'/'.join(initial)}"
            try:
                assert md._session is not None
                async with md._session.ws_connect(url, heartbeat=None, autoping=True, max_msg_size=0) as ws:
                    self.ws = ws
                    self.connected = True
                    backoff = 1.0
                    md._conn_changed()
                    if rest:
                        await self._send("SUBSCRIBE", rest)
                    rotate_at = now_ms() + ROTATE_MS
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            md._on_message(msg.data)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                        if now_ms() >= rotate_at:
                            log.info("WS %s: 23 saat doldu, planlı yeniden bağlanma", self.name)
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # ağ hataları
                md.state_message = f"WebSocket hatası: {exc.__class__.__name__}: {exc}"
                log.warning("WS %s hata: %s", self.name, exc)
            finally:
                self.ws = None
                self.connected = False
                md._conn_changed()
            if md._stopping:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
        self.connected = False


class BinanceMarketData(MarketData):
    source = "binance"

    def __init__(
        self,
        rest_base: str,
        ws_base: str,
        market_path: str,
        scanner_cfg: Callable[[], ScannerSettings],
        events: EventLog,
    ):
        super().__init__()
        self.rest_base = rest_base.rstrip("/")
        self.ws_base = ws_base.rstrip("/")
        self.market_path = market_path
        self.scanner_cfg = scanner_cfg
        self.events = events
        self._session: Optional[aiohttp.ClientSession] = None
        self._tasks: list[asyncio.Task] = []
        self._conns: list[_StreamConn] = []
        self._dyn_conn: Optional[_StreamConn] = None
        self._stopping = False
        self._weight_minute = 0
        self._weight_local = 0
        self._weight_server = 0
        self._ban_until = 0
        self._watch_refs: dict[tuple[str, str], int] = {}
        self._kl_cache: dict[tuple[str, str, int], tuple[int, list[Candle]]] = {}
        self._oi_cache: dict[str, tuple[int, Optional[float]]] = {}
        self._backfill_pending: set[tuple[str, str]] = set()
        self._lock = asyncio.Lock()

    # --- REST ----------------------------------------------------------
    async def _respect_limits(self, weight: int) -> None:
        while True:
            now = now_ms()
            if now < self._ban_until:
                await asyncio.sleep((self._ban_until - now) / 1000 + 0.5)
                continue
            minute = now // 60_000
            if minute != self._weight_minute:
                self._weight_minute = minute
                self._weight_local = 0
                self._weight_server = 0
            if max(self._weight_local, self._weight_server) + weight > WEIGHT_BUDGET:
                await asyncio.sleep(60 - (now % 60_000) / 1000 + 0.5)
                continue
            self._weight_local += weight
            return

    async def _get(self, path: str, params: Optional[dict] = None, weight: int = 1):
        await self._respect_limits(weight)
        assert self._session is not None
        async with self._session.get(self.rest_base + path, params=params) as r:
            used = r.headers.get("X-MBX-USED-WEIGHT-1M")
            if used and used.isdigit():
                self._weight_server = int(used)
            if r.status in (418, 429):
                retry = int(r.headers.get("Retry-After", "60") or 60)
                self._ban_until = now_ms() + retry * 1000
                await self.events.log("error", "rate_limit", f"Binance hız limiti (HTTP {r.status}); {retry} sn bekleniyor", toast=True, title="Hız limiti")
                raise RuntimeError(f"Binance hız limiti (HTTP {r.status})")
            if r.status >= 400:
                text = await r.text()
                raise RuntimeError(f"Binance REST {r.status}: {text[:200]}")
            return await r.json(content_type=None)

    async def _load_exchange_info(self) -> None:
        data = await self._get("/fapi/v1/exchangeInfo", weight=1)
        self.symbols = parse_exchange_info(data)

    async def _load_tickers(self) -> None:
        rows = await self._get("/fapi/v1/ticker/24hr", weight=40)
        for sym, t in parse_ticker_24h(rows).items():
            self.tickers[sym] = t

    async def _fetch_klines(self, symbol: str, tf: str, limit: int) -> list[Candle]:
        limit = max(1, min(limit, 1500))
        weight = 1 if limit < 100 else 2 if limit < 500 else 5 if limit <= 1000 else 10
        rows = await self._get("/fapi/v1/klines", {"symbol": symbol, "interval": tf, "limit": limit}, weight=weight)
        return parse_klines(rows, now_ms())

    async def _bootstrap(self, symbols: list[str], tfs: tuple[str, ...]) -> None:
        sem = asyncio.Semaphore(4)

        async def one(sym: str, tf: str) -> None:
            async with sem:
                try:
                    candles = await self._fetch_klines(sym, tf, 499)
                except Exception as exc:
                    log.warning("Geçmiş mum alınamadı %s %s: %s", sym, tf, exc)
                    return
                buf = self.buffers.get((sym, tf))
                if buf is None:
                    buf = CandleBuffer(tf)
                    self.buffers[(sym, tf)] = buf
                buf.load(candles)

        await asyncio.gather(*(one(s, tf) for s in symbols for tf in tfs))

    # --- yaşam döngüsü -------------------------------------------------
    async def start(self) -> None:
        self._stopping = False
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self._tasks.append(asyncio.create_task(self._run(), name="market-main"))

    async def stop(self) -> None:
        self._stopping = True
        for c in self._conns + ([self._dyn_conn] if self._dyn_conn else []):
            if c.task:
                c.task.cancel()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*[t for t in self._tasks], return_exceptions=True)
        if self._session:
            await self._session.close()
            self._session = None

    async def _run(self) -> None:
        delay = 5
        while not self._stopping:
            try:
                self.state = "connecting"
                self.state_message = "Sembol bilgileri ve geçmiş veriler yükleniyor"
                await self._load_exchange_info()
                await self._load_tickers()
                self.universe = select_universe(self.symbols, self.tickers, self.scanner_cfg(), now_ms())
                await self._bootstrap(self.universe, self.base_tfs)
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state = "down"
                self.state_message = f"Binance'e bağlanılamadı: {exc}"
                await self.events.log("error", "market", f"Piyasa verisi başlatılamadı: {exc}")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 120)
        if self._stopping:
            return
        streams = ["!miniTicker@arr", "!markPrice@arr@1s"]
        for s in self.universe:
            for tf in self.base_tfs:
                streams.append(f"{s.lower()}@kline_{tf}")
        for i in range(0, len(streams), STREAMS_PER_CONN):
            conn = _StreamConn(self, f"m{i // STREAMS_PER_CONN}", streams[i : i + STREAMS_PER_CONN])
            self._conns.append(conn)
            conn.start()
        self._dyn_conn = None
        await self.events.log("success", "market", f"Piyasa verisi bağlandı: {len(self.universe)} sembol taranıyor")
        self.ready.set()
        self._tasks.append(asyncio.create_task(self._refresh_loop(), name="market-refresh"))

    async def _refresh_loop(self) -> None:
        last_info = now_ms()
        while not self._stopping:
            await asyncio.sleep(600)
            try:
                if now_ms() - last_info > 3600_000:
                    await self._load_exchange_info()
                    last_info = now_ms()
                new_universe = select_universe(self.symbols, self.tickers, self.scanner_cfg(), now_ms())
                added = [s for s in new_universe if s not in self.universe]
                self.universe = new_universe
                if added:
                    await self._bootstrap(added, self.base_tfs)
                    streams = [f"{s.lower()}@kline_{tf}" for s in added for tf in self.base_tfs]
                    await self._add_streams(streams)
                    await self.events.log("info", "market", f"Evrene eklenen semboller: {', '.join(added[:10])}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Evren yenilemesi başarısız: %s", exc)

    async def _add_streams(self, streams: list[str]) -> None:
        for conn in self._conns:
            room = STREAMS_PER_CONN - len(conn.streams)
            if room <= 0:
                continue
            part, streams = streams[:room], streams[room:]
            await conn.add(part)
            if not streams:
                return
        if streams:
            conn = _StreamConn(self, f"m{len(self._conns)}", streams)
            self._conns.append(conn)
            conn.start()

    def _conn_changed(self) -> None:
        conns = self._conns
        if conns and all(c.connected for c in conns):
            self.state = "ok"
            self.state_message = ""
        elif any(c.connected for c in conns):
            self.state = "connecting"
        else:
            self.state = "down"

    # --- mesaj işleme --------------------------------------------------
    def _on_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if isinstance(msg, dict) and "id" in msg and ("result" in msg or "error" in msg):
            if msg.get("error"):
                log.warning("WS abonelik hatası: %s", msg["error"])
            return
        self.last_msg_ts = now_ms()
        payload = msg.get("data", msg) if isinstance(msg, dict) else msg
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    self._route(item)
        elif isinstance(payload, dict):
            self._route(payload)

    def _route(self, p: dict) -> None:
        e = p.get("e")
        try:
            if e == "24hrMiniTicker":
                last = float(p["c"])
                op = float(p.get("o", last))
                self.tickers[p["s"]] = Ticker(
                    last=last,
                    open=op,
                    high=float(p.get("h", last)),
                    low=float(p.get("l", last)),
                    quote_volume=float(p.get("q", 0.0)),
                    change_pct=((last / op - 1.0) * 100.0) if op else 0.0,
                    ts=int(p.get("E", now_ms())),
                )
            elif e == "markPriceUpdate":
                self.marks[p["s"]] = MarkInfo(
                    mark=float(p["p"]),
                    index=float(p.get("i", p["p"]) or p["p"]),
                    funding_rate=float(p.get("r", 0.0) or 0.0),
                    next_funding_time=int(p.get("T", 0) or 0),
                    ts=int(p.get("E", now_ms())),
                )
            elif e == "kline":
                k = p["k"]
                symbol = p.get("s", k.get("s"))
                tf = k["i"]
                candle = Candle(
                    t=int(k["t"]),
                    o=float(k["o"]),
                    h=float(k["h"]),
                    l=float(k["l"]),
                    c=float(k["c"]),
                    v=float(k.get("v", 0.0)),
                    qv=float(k.get("q", 0.0)),
                    tbq=float(k.get("Q", 0.0)),
                    closed=bool(k.get("x", False)),
                )
                buf = self.buffers.get((symbol, tf))
                if buf is None:
                    buf = CandleBuffer(tf)
                    self.buffers[(symbol, tf)] = buf
                closed = buf.update(candle)
                self._emit_kline(symbol, tf, candle)
                if buf.gap_detected and (symbol, tf) not in self._backfill_pending:
                    self._backfill_pending.add((symbol, tf))
                    asyncio.get_running_loop().create_task(self._backfill(symbol, tf))
                if closed is not None:
                    asyncio.get_running_loop().create_task(self._emit_bar_close(symbol, tf, closed))
        except (KeyError, ValueError, TypeError) as exc:
            log.debug("Mesaj ayrıştırılamadı: %s", exc)

    async def _backfill(self, symbol: str, tf: str) -> None:
        try:
            candles = await self._fetch_klines(symbol, tf, 60)
            buf = self.buffers.get((symbol, tf))
            if buf:
                buf.merge_closed([c for c in candles if c.closed])
        except Exception as exc:
            log.warning("Boşluk doldurma başarısız %s %s: %s", symbol, tf, exc)
        finally:
            self._backfill_pending.discard((symbol, tf))

    # --- grafik ve sorgular ---------------------------------------------
    def _is_base(self, symbol: str, tf: str) -> bool:
        return symbol in self.universe and tf in self.base_tfs

    async def watch(self, symbol: str, tf: str) -> None:
        key = (symbol, tf)
        self._watch_refs[key] = self._watch_refs.get(key, 0) + 1
        if self._watch_refs[key] > 1 or self._is_base(symbol, tf) or symbol not in self.symbols:
            return
        async with self._lock:
            try:
                candles = await self._fetch_klines(symbol, tf, 499)
                buf = CandleBuffer(tf)
                buf.load(candles)
                self.buffers[key] = buf
            except Exception as exc:
                log.warning("Grafik verisi alınamadı %s %s: %s", symbol, tf, exc)
            stream = f"{symbol.lower()}@kline_{tf}"
            if self._dyn_conn is None:
                self._dyn_conn = _StreamConn(self, "dyn", [stream])
                self._dyn_conn.start()
            else:
                await self._dyn_conn.add([stream])

    async def unwatch(self, symbol: str, tf: str) -> None:
        key = (symbol, tf)
        n = self._watch_refs.get(key, 0) - 1
        if n > 0:
            self._watch_refs[key] = n
            return
        self._watch_refs.pop(key, None)
        if self._is_base(symbol, tf):
            return

        async def later() -> None:
            await asyncio.sleep(120)
            if self._watch_refs.get(key, 0) == 0 and not self._is_base(symbol, tf) and self._dyn_conn:
                await self._dyn_conn.remove([f"{symbol.lower()}@kline_{tf}"])
                self.buffers.pop(key, None)

        asyncio.get_running_loop().create_task(later())

    async def klines(self, symbol: str, tf: str, limit: int = 300) -> list[Candle]:
        buf = self.buffers.get((symbol, tf))
        if buf is not None and len(buf) >= min(limit, 400) - 1:
            return buf.chart(limit)
        key = (symbol, tf, limit)
        cached = self._kl_cache.get(key)
        if cached and now_ms() - cached[0] < 15_000:
            return cached[1]
        candles = await self._fetch_klines(symbol, tf, limit)
        self._kl_cache[key] = (now_ms(), candles)
        if len(self._kl_cache) > 200:
            self._kl_cache.pop(next(iter(self._kl_cache)))
        return candles

    async def book(self, symbol: str) -> Optional[tuple[float, float]]:
        try:
            d = await self._get("/fapi/v1/ticker/bookTicker", {"symbol": symbol}, weight=2)
            return float(d["bidPrice"]), float(d["askPrice"])
        except Exception as exc:
            log.debug("bookTicker alınamadı %s: %s", symbol, exc)
            return None

    async def oi_change_pct(self, symbol: str, minutes: int = 60) -> Optional[float]:
        cached = self._oi_cache.get(symbol)
        if cached and now_ms() - cached[0] < 300_000:
            return cached[1]
        value: Optional[float] = None
        try:
            limit = max(2, minutes // 5 + 1)
            rows = await self._get(
                "/futures/data/openInterestHist", {"symbol": symbol, "period": "5m", "limit": limit}, weight=1
            )
            if isinstance(rows, list) and len(rows) >= 2:
                first = float(rows[0]["sumOpenInterest"])
                last = float(rows[-1]["sumOpenInterest"])
                value = (last / first - 1.0) * 100.0 if first > 0 else None
        except Exception as exc:
            log.debug("OI geçmişi alınamadı %s: %s", symbol, exc)
        self._oi_cache[symbol] = (now_ms(), value)
        return value
