"""Binance USDⓈ-M broker (testnet/demo ve canlı).

Önemli API notları:
- Koşullu emirler (STOP_MARKET, TAKE_PROFIT_MARKET, TRAILING_STOP_MARKET...) 2025-12-09'dan
  beri Algo servisi üzerinden gönderilir: POST /fapi/v1/algoOrder (algoType=CONDITIONAL).
  Eski /fapi/v1/order uç noktası bu tipler için -4120 hatası verir.
- Kullanıcı veri akışı 2026 yapısında /private yolundadır (yol ayarlardan değiştirilebilir).
- Zaman aşımı veya 5xx yanıtında emir durumu bilinmez: emir körlemesine tekrar
  gönderilmez, önce clientOrderId ile sorgulanır.
"""
from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal
from typing import Optional

import aiohttp

from ..market.base import MarketData
from ..util import now_ms
from .binance_client import BinanceAPIError, BinanceREST
from .broker_base import (
    AccountInfo,
    Broker,
    BrokerError,
    BrokerPosition,
    Fill,
    OpenRequest,
    ReduceEvent,
    TPOrder,
    default_mmr_cap,
)
from .filters import D, check_qty, floor_step, fmt, round_tick

log = logging.getLogger(__name__)

LISTEN_KEY_KEEPALIVE_S = 30 * 60
USER_WS_ROTATE_MS = 23 * 3600 * 1000


class BinanceBroker(Broker):
    def __init__(
        self,
        env: str,
        rest: BinanceREST,
        ws_base: str,
        user_stream_path: str,
        market: MarketData,
    ):
        super().__init__()
        self.kind = env
        self.rest = rest
        self.ws_base = ws_base.rstrip("/")
        self.user_stream_path = user_stream_path
        self.market = market
        self.user_stream_state = "connecting"
        self._tasks: list[asyncio.Task] = []
        self._pos_ids: dict[str, str] = {}
        self._sides: dict[str, str] = {}
        self._sl_ids: dict[str, str] = {}
        self._tp_ids: dict[str, list[str]] = {}
        self._seq: dict[str, int] = {}
        self._fees: dict[str, tuple[float, float]] = {}
        self._brackets: dict[str, list[dict]] = {}
        self._prepared: dict[str, tuple[str, int]] = {}
        self._stopping = False

    # --- kayıt ---------------------------------------------------------
    def register(self, symbol: str, position_id: str, side: str) -> None:
        """Motor, yeniden başlatmadan sonra açık pozisyonları broker'a tanıtır."""
        self._pos_ids[symbol] = position_id
        self._sides[symbol] = side

    def _cid(self, symbol: str, suffix: str) -> str:
        pid = self._pos_ids.get(symbol, "x")[-10:]
        return f"bb-{pid}-{suffix}"

    def _next(self, symbol: str, kind: str) -> int:
        key = f"{symbol}:{kind}"
        self._seq[key] = self._seq.get(key, 0) + 1
        return self._seq[key]

    # --- yaşam döngüsü -------------------------------------------------
    async def start(self) -> None:
        self._stopping = False
        await self.rest.start()
        await self.account()  # anahtarları doğrular
        try:
            await self.rest.request("POST", "/fapi/v1/positionSide/dual", {"dualSidePosition": False}, signed=True)
        except BinanceAPIError as exc:
            if exc.code != -4059:
                log.warning("Pozisyon modu ayarlanamadı: %s", exc.describe())
        try:
            await self.rest.request("POST", "/fapi/v1/multiAssetsMargin", {"multiAssetsMargin": False}, signed=True)
        except BinanceAPIError as exc:
            log.debug("Çoklu varlık modu ayarı: %s", exc.describe())
        self._tasks.append(asyncio.create_task(self._user_stream_loop(), name="user-stream"))
        self._tasks.append(asyncio.create_task(self._keepalive_loop(), name="listenkey-keepalive"))

    async def stop(self) -> None:
        self._stopping = True
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.rest.close()

    # --- hesap ---------------------------------------------------------
    async def account(self) -> AccountInfo:
        d = await self.rest.request("GET", "/fapi/v3/account", signed=True)
        wallet = float(d.get("totalWalletBalance", 0.0))
        unreal = float(d.get("totalUnrealizedProfit", 0.0))
        return AccountInfo(
            equity=wallet + unreal,
            wallet=wallet,
            unrealized=unreal,
            available=float(d.get("availableBalance", 0.0)),
            margin_used=float(d.get("totalInitialMargin", 0.0)),
        )

    async def positions(self) -> dict[str, BrokerPosition]:
        rows = await self.rest.request("GET", "/fapi/v3/positionRisk", signed=True)
        out: dict[str, BrokerPosition] = {}
        for r in rows or []:
            amt = float(r.get("positionAmt", 0.0))
            if amt == 0:
                continue
            out[r["symbol"]] = BrokerPosition(
                symbol=r["symbol"],
                side="LONG" if amt > 0 else "SHORT",
                qty=abs(amt),
                entry_price=float(r.get("entryPrice", 0.0)),
                liq_price=float(r.get("liquidationPrice", 0.0) or 0.0),
                unrealized=float(r.get("unRealizedProfit", 0.0) or 0.0),
                margin=float(r.get("isolatedMargin", 0.0) or r.get("initialMargin", 0.0) or 0.0),
            )
        return out

    async def fee_rates(self, symbol: str) -> tuple[float, float]:
        if symbol in self._fees:
            return self._fees[symbol]
        try:
            d = await self.rest.request("GET", "/fapi/v1/commissionRate", {"symbol": symbol}, signed=True)
            rates = (float(d["makerCommissionRate"]), float(d["takerCommissionRate"]))
        except (BinanceAPIError, KeyError, ValueError):
            rates = (0.0002, 0.0005)
        self._fees[symbol] = rates
        return rates

    async def mmr_and_cap(self, symbol: str, notional: float, cls: str) -> tuple[float, int]:
        brackets = self._brackets.get(symbol)
        if brackets is None:
            try:
                d = await self.rest.request("GET", "/fapi/v1/leverageBracket", {"symbol": symbol}, signed=True)
                item = d[0] if isinstance(d, list) else d
                brackets = list(item.get("brackets", []))
            except (BinanceAPIError, KeyError, IndexError, TypeError):
                brackets = []
            self._brackets[symbol] = brackets
        for b in brackets:
            if float(b.get("notionalFloor", 0)) <= notional < float(b.get("notionalCap", 1e18)):
                return float(b.get("maintMarginRatio", 0.01)), int(b.get("initialLeverage", 1))
        return default_mmr_cap(cls)

    # --- emir yardımcıları ---------------------------------------------
    async def _prepare(self, symbol: str, leverage: int) -> None:
        if self._prepared.get(symbol) == ("ISOLATED", leverage):
            return
        try:
            await self.rest.request("POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": "ISOLATED"}, signed=True)
        except BinanceAPIError as exc:
            if exc.code != -4046:
                raise BrokerError(f"Marjin tipi ayarlanamadı: {exc.describe()}") from exc
        try:
            await self.rest.request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage}, signed=True)
        except BinanceAPIError as exc:
            raise BrokerError(f"Kaldıraç ayarlanamadı: {exc.describe()}") from exc
        self._prepared[symbol] = ("ISOLATED", leverage)

    async def _query_order(self, symbol: str, cid: str) -> Optional[dict]:
        try:
            return await self.rest.request("GET", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": cid}, signed=True)
        except BinanceAPIError as exc:
            if exc.code == -2013:  # emir yok
                return None
            raise

    async def _send_order(self, params: dict) -> dict:
        """Emri gönderir; durum bilinmiyorsa clientOrderId ile sorgular (çift emir önlemi)."""
        try:
            return await self.rest.request("POST", "/fapi/v1/order", params, signed=True)
        except BinanceAPIError as exc:
            if not exc.unknown:
                raise
            for _ in range(3):
                await asyncio.sleep(1.0)
                try:
                    found = await self._query_order(params["symbol"], params["newClientOrderId"])
                except BinanceAPIError:
                    continue
                if found is not None:
                    return found
                break
            raise

    def _fee_usdt(self, o: dict, qty: float, price: float) -> float:
        try:
            n = float(o.get("n", 0.0) or 0.0)
        except ValueError:
            n = 0.0
        asset = o.get("N") or "USDT"
        if asset == "USDT":
            return n
        px = self.market.price(f"{asset}USDT")
        if px:
            return n * px
        return qty * price * 0.0005

    # --- pozisyon açma -------------------------------------------------
    async def open(self, req: OpenRequest) -> Fill:
        sym = req.symbol
        info = self.market.symbols.get(sym)
        if info is None:
            raise BrokerError(f"{sym} sembol bilgisi yok")
        self.register(sym, req.position_id, req.side)
        try:
            return await self._open_registered(req, info)
        except BaseException:
            # Pozisyon hiç açılmadıysa kaydı geri al (borsadaki ilgisiz işlemler bota atfedilmesin)
            if sym not in self._sl_ids:
                self._sides.pop(sym, None)
                self._pos_ids.pop(sym, None)
            raise

    async def _open_registered(self, req: OpenRequest, info) -> Fill:
        sym = req.symbol
        await self._prepare(sym, req.leverage)
        side = "BUY" if req.side == "LONG" else "SELL"
        params: dict = {
            "symbol": sym,
            "side": side,
            "quantity": fmt(req.qty),
            "newClientOrderId": self._cid(sym, "E"),
            "newOrderRespType": "RESULT",
        }
        if req.entry_mode == "ioc":
            mult = 1 + req.max_slippage_pct if side == "BUY" else 1 - req.max_slippage_pct
            limit = round_tick(req.ref_price * mult, info.tick_size, "down" if side == "BUY" else "up")
            params.update({"type": "LIMIT", "timeInForce": "IOC", "price": fmt(limit)})
        else:
            params["type"] = "MARKET"
        try:
            res = await self._send_order(params)
        except BinanceAPIError as exc:
            raise BrokerError(f"Giriş emri reddedildi: {exc.describe()}") from exc
        executed = D(res.get("executedQty", "0") or "0")
        if executed <= 0:
            raise BrokerError("Giriş emri dolmadı (fiyat kayma sınırını aştı)")
        avg = float(res.get("avgPrice") or 0.0) or float(res.get("price") or 0.0) or req.ref_price
        _, taker = await self.fee_rates(sym)

        try:
            await self._place_stop(sym, req.side, req.stop_price)
        except Exception as exc:
            log.error("SL yerleştirilemedi, pozisyon güvenlik için kapatılıyor: %s", exc)
            try:
                await self._market_close(sym, req.side, executed)
                await self.cleanup(sym)
            finally:
                if self.on_warning:
                    await self.on_warning(f"{sym}: stop-loss yerleştirilemedi, pozisyon güvenlik için kapatıldı")
            raise BrokerError(f"Stop-loss yerleştirilemedi; pozisyon kapatıldı: {exc}") from exc

        scale = executed / req.qty if req.qty > 0 else Decimal(1)
        tps = [TPOrder(t.index, t.price, floor_step(t.qty * scale, info.step_size)) for t in req.tps]
        await self._place_tps(sym, req.side, tps)
        return Fill(price=avg, qty=float(executed), fee=float(executed) * avg * taker, ts=now_ms())

    async def _place_stop(self, sym: str, side: str, stop: Decimal) -> Optional[str]:
        cid = self._cid(sym, f"S{self._next(sym, 'S')}")
        params = {
            "algoType": "CONDITIONAL",
            "symbol": sym,
            "side": "SELL" if side == "LONG" else "BUY",
            "type": "STOP_MARKET",
            "triggerPrice": fmt(stop),
            "closePosition": True,
            "workingType": "MARK_PRICE",
            "priceProtect": True,
            "clientAlgoId": cid,
        }
        last_exc: Optional[Exception] = None
        for _ in range(3):
            try:
                await self.rest.request("POST", "/fapi/v1/algoOrder", params, signed=True)
                old = self._sl_ids.get(sym)
                self._sl_ids[sym] = cid
                return old
            except BinanceAPIError as exc:
                last_exc = exc
                if not exc.unknown:
                    raise BrokerError(exc.describe()) from exc
                await asyncio.sleep(0.8)
        raise BrokerError(f"Stop-loss emri gönderilemedi: {last_exc}")

    async def _place_tps(self, sym: str, side: str, tps: list[TPOrder]) -> None:
        info = self.market.symbols.get(sym)
        ids: list[str] = []
        for t in tps:
            if info is not None and check_qty(info, t.qty, float(t.price), market=False, reduce_only=True):
                continue
            cid = self._cid(sym, f"T{t.index}-{self._next(sym, 'T')}")
            params = {
                "symbol": sym,
                "side": "SELL" if side == "LONG" else "BUY",
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": fmt(t.qty),
                "price": fmt(t.price),
                "reduceOnly": True,
                "newClientOrderId": cid,
            }
            try:
                await self._send_order(params)
                ids.append(cid)
            except BinanceAPIError as exc:
                log.warning("TP%s yerleştirilemedi (%s): %s", t.index, sym, exc.describe())
                if self.on_warning:
                    await self.on_warning(f"{sym}: TP{t.index} emri yerleştirilemedi ({exc.describe()})")
        self._tp_ids[sym] = ids

    async def replace_stop(self, symbol: str, side: str, stop: Decimal) -> None:
        # Önce yeni stop, sonra eskisini iptal: pozisyon hiçbir an korumasız kalmaz.
        try:
            old = await self._place_stop(symbol, side, stop)
        except BrokerError:
            raise
        if old:
            try:
                await self.rest.request("DELETE", "/fapi/v1/algoOrder", {"symbol": symbol, "clientAlgoId": old}, signed=True)
            except BinanceAPIError as exc:
                log.info("Eski stop iptal edilemedi (%s): %s", symbol, exc.describe())

    async def replace_tps(self, symbol: str, side: str, tps: list[TPOrder]) -> None:
        for cid in self._tp_ids.get(symbol, []):
            try:
                await self.rest.request("DELETE", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": cid}, signed=True)
            except BinanceAPIError as exc:
                log.info("TP iptal edilemedi (%s): %s", cid, exc.describe())
        await self._place_tps(symbol, side, tps)

    async def _market_close(self, sym: str, side: str, qty: Decimal) -> dict:
        params = {
            "symbol": sym,
            "side": "SELL" if side == "LONG" else "BUY",
            "type": "MARKET",
            "quantity": fmt(qty),
            "reduceOnly": True,
            "newClientOrderId": self._cid(sym, f"X{self._next(sym, 'X')}"),
            "newOrderRespType": "RESULT",
        }
        return await self._send_order(params)

    async def close(self, symbol: str, side: str, qty: Optional[Decimal], tag: str) -> Optional[Fill]:
        if qty is None:
            pos = (await self.positions()).get(symbol)
            if pos is None:
                return None
            qty = D(pos.qty)
        try:
            res = await self._market_close(symbol, side, qty)
        except BinanceAPIError as exc:
            raise BrokerError(f"Kapatma emri reddedildi: {exc.describe()}") from exc
        executed = float(res.get("executedQty", 0.0) or 0.0)
        avg = float(res.get("avgPrice") or 0.0) or (self.market.price(symbol) or 0.0)
        _, taker = await self.fee_rates(symbol)
        return Fill(price=avg, qty=executed, fee=executed * avg * taker, ts=now_ms())

    async def cleanup(self, symbol: str) -> None:
        for path in ("/fapi/v1/allOpenOrders", "/fapi/v1/algoOpenOrders"):
            try:
                await self.rest.request("DELETE", path, {"symbol": symbol}, signed=True)
            except BinanceAPIError as exc:
                log.info("Açık emirler temizlenemedi (%s %s): %s", symbol, path, exc.describe())
        self._sl_ids.pop(symbol, None)
        self._tp_ids.pop(symbol, None)
        self._sides.pop(symbol, None)
        self._pos_ids.pop(symbol, None)

    async def has_stop(self, symbol: str) -> Optional[bool]:
        """Sembolde botun açık stop emri var mı? Sorgulanamazsa None."""
        for path in ("/fapi/v1/openAlgoOrders",):
            try:
                rows = await self.rest.request("GET", path, {"symbol": symbol}, signed=True)
            except BinanceAPIError:
                return None
            items = rows if isinstance(rows, list) else (rows or {}).get("orders", [])
            for o in items:
                cid = str(o.get("clientAlgoId", ""))
                otype = str(o.get("orderType", o.get("type", "")))
                if cid.startswith("bb-") and "STOP" in otype:
                    return True
            return False
        return None

    # --- kullanıcı veri akışı ------------------------------------------
    async def _keepalive_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(LISTEN_KEY_KEEPALIVE_S)
            try:
                await self.rest.request("PUT", "/fapi/v1/listenKey", key_only=True)
            except BinanceAPIError as exc:
                log.warning("listenKey uzatılamadı: %s", exc.describe())

    async def _user_stream_loop(self) -> None:
        backoff = 1.0
        while not self._stopping:
            try:
                d = await self.rest.request("POST", "/fapi/v1/listenKey", key_only=True)
                key = d["listenKey"]
                url = self.ws_base + self.user_stream_path.replace("{listenKey}", key)
                session = self.rest.session
                assert session is not None
                async with session.ws_connect(url, heartbeat=None, autoping=True, max_msg_size=0) as ws:
                    self.user_stream_state = "ok"
                    backoff = 1.0
                    started = now_ms()
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            payload = json.loads(msg.data)
                            data = payload.get("data", payload) if isinstance(payload, dict) else payload
                            if isinstance(data, dict) and data.get("e") == "listenKeyExpired":
                                break
                            if isinstance(data, dict):
                                await self._on_user_event(data)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                        if now_ms() - started > USER_WS_ROTATE_MS:
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Kullanıcı akışı hatası: %s", exc)
            self.user_stream_state = "connecting"
            if self._stopping:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _on_user_event(self, d: dict) -> None:
        e = d.get("e")
        ts = int(d.get("E", now_ms()) or now_ms())
        if e == "ORDER_TRADE_UPDATE":
            o = d.get("o", {})
            if o.get("x") != "TRADE":
                return
            sym = o.get("s", "")
            pos_side = self._sides.get(sym)
            if not pos_side:
                return
            order_side = o.get("S")
            reduces = (pos_side == "LONG" and order_side == "SELL") or (pos_side == "SHORT" and order_side == "BUY")
            if not reduces:
                return
            qty = float(o.get("l", 0.0) or 0.0)
            price = float(o.get("L", 0.0) or 0.0)
            if qty <= 0:
                return
            rp = o.get("rp")
            cid = str(o.get("c", ""))
            prefix = self._cid(sym, "")
            kind, tp_index = "sl", None
            if cid.startswith(prefix + "T"):
                kind = "tp"
                try:
                    tp_index = int(cid[len(prefix) + 1 :].split("-")[0])
                except ValueError:
                    tp_index = None
            elif cid.startswith(prefix + "X"):
                return  # motorun kendi kapatma emri; motor senkron olarak işler
            elif cid.startswith("autoclose-"):
                kind = "liquidation"
            elif cid.startswith("adl_autoclose"):
                kind = "adl"
            elif cid.startswith("settlement_autoclose"):
                kind = "delist"
            ev = ReduceEvent(
                symbol=sym,
                qty=qty,
                price=price,
                fee=self._fee_usdt(o, qty, price),
                realized_pnl=float(rp) if rp is not None else None,
                kind=kind,
                ts=ts,
                tp_index=tp_index,
            )
            if self.on_reduce:
                await self.on_reduce(ev)
        elif e == "ACCOUNT_UPDATE":
            a = d.get("a", {})
            if a.get("m") != "FUNDING_FEE":
                return
            change = 0.0
            for b in a.get("B", []):
                if b.get("a") == "USDT":
                    change += float(b.get("bc", 0.0) or 0.0)
            for p in a.get("P", []):
                sym = p.get("s", "")
                if sym in self._sides and self.on_funding and change:
                    await self.on_funding(sym, change, ts)
                    break
        elif e == "ALGO_UPDATE":
            o = d.get("o", d)
            status = str(o.get("X", o.get("status", "")))
            cid = str(o.get("caid", o.get("clientAlgoId", "")))
            if cid.startswith("bb-") and status in ("REJECTED", "EXPIRED") and self.on_warning:
                await self.on_warning(f"Koşullu emir {status}: {cid}")
        elif e == "MARGIN_CALL" and self.on_warning:
            await self.on_warning("Marjin çağrısı alındı! Pozisyonları kontrol edin.")
