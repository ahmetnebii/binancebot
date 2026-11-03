"""SADECE GELİŞTİRME VE TEST İÇİN sentetik piyasa verisi.

Bu kaynak gerçek piyasayı temsil ETMEZ. Binance'e erişim olmayan ortamlarda arayüzü,
motoru ve testleri çalıştırmak için kullanılır. BB_DATA_SOURCE=synthetic ile açılır ve
sistemi zorunlu olarak simülasyon (paper) ortamında tutar; arayüzde kalıcı uyarı gösterilir.
"""
from __future__ import annotations

import asyncio
import math
from typing import Optional

import numpy as np

from ..models import Candle, SymbolInfo
from ..util import TF_MS, now_ms
from .base import CandleBuffer, MarketData, MarkInfo, Ticker

TFS = ("1m", "5m", "15m", "1h", "4h")
HISTORY_DAYS = 14

# sembol, fiyat, tick, step, dakikalık oynaklık, BTC betası, dakikalık sürüklenme, dakikalık USDT hacmi, senaryo
SPECS: list[tuple[str, float, str, str, float, float, float, float, Optional[str]]] = [
    ("BTCUSDT", 68250.0, "0.10", "0.001", 0.0008, 1.00, 0.000006, 9_000_000, None),
    ("ETHUSDT", 3420.0, "0.01", "0.001", 0.0010, 1.10, 0.000004, 4_500_000, None),
    ("SOLUSDT", 162.4, "0.010", "0.01", 0.0014, 1.30, 0.000016, 1_800_000, "breakout_long"),
    ("BNBUSDT", 598.0, "0.010", "0.01", 0.0009, 0.80, 0.0, 800_000, None),
    ("XRPUSDT", 0.6234, "0.0001", "0.1", 0.0013, 1.10, -0.000012, 1_200_000, None),
    ("DOGEUSDT", 0.16412, "0.00001", "1", 0.0016, 1.30, 0.00001, 1_400_000, None),
    ("ADAUSDT", 0.4521, "0.0001", "1", 0.0014, 1.10, -0.000014, 600_000, "breakdown_short"),
    ("AVAXUSDT", 35.62, "0.001", "0.1", 0.0016, 1.30, 0.000016, 700_000, "breakout_long"),
    ("LINKUSDT", 15.284, "0.001", "0.01", 0.0015, 1.20, 0.000015, 650_000, "breakout_long"),
    ("DOTUSDT", 6.512, "0.001", "0.1", 0.0014, 1.10, -0.00001, 400_000, None),
    ("LTCUSDT", 84.73, "0.01", "0.001", 0.0012, 0.90, -0.000006, 450_000, None),
    ("NEARUSDT", 5.493, "0.001", "1", 0.0017, 1.30, 0.000012, 500_000, None),
    ("APTUSDT", 9.118, "0.001", "0.1", 0.0017, 1.30, -0.000012, 350_000, None),
    ("ARBUSDT", 0.9124, "0.0001", "0.1", 0.0017, 1.30, -0.000016, 420_000, "breakdown_short"),
    ("OPUSDT", 2.1342, "0.0001", "0.1", 0.0017, 1.30, -0.000008, 380_000, None),
    ("SUIUSDT", 1.6231, "0.0001", "0.1", 0.0019, 1.40, 0.000014, 900_000, None),
    ("INJUSDT", 25.41, "0.001", "0.1", 0.0018, 1.30, 0.000006, 300_000, None),
    ("TIAUSDT", 7.023, "0.001", "0.1", 0.0019, 1.40, -0.00001, 280_000, None),
    ("FILUSDT", 5.124, "0.001", "0.1", 0.0015, 1.10, -0.000006, 260_000, None),
    ("ATOMUSDT", 8.043, "0.001", "0.01", 0.0014, 1.10, 0.000004, 240_000, None),
    ("UNIUSDT", 9.312, "0.001", "1", 0.0015, 1.10, 0.000008, 270_000, None),
    ("AAVEUSDT", 152.37, "0.01", "0.1", 0.0016, 1.20, 0.000012, 320_000, None),
    ("TONUSDT", 6.0412, "0.0001", "0.1", 0.0012, 0.90, -0.000004, 220_000, None),
    ("TRXUSDT", 0.13241, "0.00001", "1", 0.0008, 0.60, 0.000002, 300_000, None),
    ("ETCUSDT", 25.112, "0.001", "0.01", 0.0013, 1.00, -0.000008, 210_000, None),
    ("FETUSDT", 1.4121, "0.0001", "1", 0.0020, 1.40, 0.00001, 330_000, None),
]


def _aggregate(t: np.ndarray, o, h, l, c, v, qv, tbq, tf: str, current_minute: int) -> list[Candle]:  # noqa: E741
    step = TF_MS[tf]
    keys = t - (t % step)
    starts = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]])
    out: list[Candle] = []
    o_a = o[starts]
    c_a = c[np.r_[starts[1:] - 1, len(t) - 1]]
    h_a = np.maximum.reduceat(h, starts)
    l_a = np.minimum.reduceat(l, starts)
    v_a = np.add.reduceat(v, starts)
    qv_a = np.add.reduceat(qv, starts)
    tbq_a = np.add.reduceat(tbq, starts)
    for i, s in enumerate(starts):
        bt = int(keys[s])
        out.append(
            Candle(
                t=bt, o=float(o_a[i]), h=float(h_a[i]), l=float(l_a[i]), c=float(c_a[i]),
                v=float(v_a[i]), qv=float(qv_a[i]), tbq=float(tbq_a[i]),
                closed=bt + step <= current_minute,
            )
        )
    return out


class _SymState:
    __slots__ = ("price", "sigma", "beta", "drift", "qv_min", "funding", "cur")

    def __init__(self, price: float, sigma: float, beta: float, drift: float, qv_min: float, funding: float):
        self.price = price
        self.sigma = sigma
        self.beta = beta
        self.drift = drift
        self.qv_min = qv_min
        self.funding = funding
        self.cur: dict[str, Candle] = {}


class SyntheticMarketData(MarketData):
    source = "synthetic"

    def __init__(self, seed: int = 7, tick_interval: float = 1.0):
        super().__init__()
        self.rng = np.random.default_rng(seed)
        self.tick_interval = tick_interval
        self._state: dict[str, _SymState] = {}
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self._scenario: dict[str, Optional[str]] = {}

    # --- geçmiş üretimi ------------------------------------------------
    def _generate(self) -> None:
        rng = self.rng
        now = now_ms()
        cur_min = now - (now % 60_000)
        n = HISTORY_DAYS * 1440 + 1  # son eleman oluşmakta olan dakika
        t = cur_min - (n - 1) * 60_000 + np.arange(n, dtype=np.int64) * 60_000
        l15 = now - (now % TF_MS["15m"])
        i_l15 = int((l15 - t[0]) // 60_000)
        b_start, b_end = i_l15 - 15, i_l15
        c_start = b_start - 720
        btc_shock = rng.standard_normal(n)
        vol_regime = np.repeat(rng.uniform(0.75, 1.35, size=n // 720 + 1), 720)[:n]

        for sym, price, tick, step, sigma, beta, drift, qv_min, scen in SPECS:
            self._scenario[sym] = scen
            idio = rng.standard_normal(n)
            if sym == "BTCUSDT":
                r = drift + sigma * vol_regime * btc_shock
            else:
                r = drift + beta * 0.0008 * vol_regime * btc_shock * 0.6 + sigma * 0.8 * vol_regime * idio
            closes = np.empty(n)
            closes[0] = 1.0
            closes[1:] = np.exp(np.cumsum(r[1:]))
            wick = np.full(n, 0.35)
            vol_mult = np.ones(n)
            taker = np.clip(0.5 + 0.15 * r / sigma + 0.04 * rng.standard_normal(n), 0.15, 0.85)
            if scen in ("breakout_long", "breakdown_short") and c_start > 10:
                sgn = 1.0 if scen == "breakout_long" else -1.0
                level = closes[c_start - 1]
                x = 0.0
                for i in range(c_start, b_start):
                    x = float(np.clip(0.95 * x + 0.0009 * rng.standard_normal(), -0.0042, 0.0042))
                    closes[i] = level * math.exp(x)
                wick[c_start:b_start] = 0.08
                vol_mult[c_start:b_start] = 0.55
                ramp = np.linspace(0.0, sgn * 0.0115, b_end - b_start + 1)[1:]
                base = closes[b_start - 1]
                for j, i in enumerate(range(b_start, b_end)):
                    closes[i] = base * math.exp(ramp[j] + 0.0004 * rng.standard_normal())
                closes[b_end - 1] = base * math.exp(sgn * 0.0115)
                wick[b_start:b_end] = 0.12
                vol_mult[b_start:b_end] = 5.0
                taker[b_start:b_end] = 0.68 if sgn > 0 else 0.32
                tail = closes[b_end - 1]
                for i in range(b_end, n):
                    tail *= math.exp(sgn * 0.00005 + sigma * 0.5 * rng.standard_normal())
                    closes[i] = tail
            closes *= price / closes[-1]
            opens = np.empty(n)
            opens[1:] = closes[:-1]
            opens[0] = closes[0]
            body_hi = np.maximum(opens, closes)
            body_lo = np.minimum(opens, closes)
            highs = body_hi * np.exp(np.abs(rng.standard_normal(n)) * sigma * wick)
            lows = body_lo * np.exp(-np.abs(rng.standard_normal(n)) * sigma * wick)
            rr = np.log(closes / opens)
            qv = qv_min * np.exp(0.35 * rng.standard_normal(n)) * (1.0 + 1.5 * np.abs(rr) / sigma) * vol_mult
            v = qv / closes
            tbq = qv * taker

            info = SymbolInfo(
                symbol=sym, base=sym[:-4], quote="USDT", tick_size=tick, step_size=step,
                market_step_size=step, min_qty=step, max_qty="10000000", market_max_qty="1000000",
                min_notional="5", onboard_date=now - 400 * 86_400_000, delivery_date=4133404800000,
                status="TRADING", contract_type="PERPETUAL",
            )
            self.symbols[sym] = info
            for tf in TFS:
                candles = _aggregate(t, opens, highs, lows, closes, v, qv, tbq, tf, cur_min)
                buf = CandleBuffer(tf)
                buf.load(candles)
                self.buffers[(sym, tf)] = buf
            st = _SymState(float(closes[-1]), sigma, beta, drift, qv_min, float(rng.normal(0.0001, 0.00008)))
            for tf in TFS:
                cur = self.buffers[(sym, tf)].current
                if cur is None:
                    bt = now - (now % TF_MS[tf])
                    cur = Candle(t=bt, o=st.price, h=st.price, l=st.price, c=st.price, closed=False)
                st.cur[tf] = cur
            self._state[sym] = st
        self.universe = [s[0] for s in SPECS]
        self._refresh_tickers()
        self._refresh_marks()

    def _refresh_tickers(self) -> None:
        ts = now_ms()
        for sym, st in self._state.items():
            buf = self.buffers[(sym, "1h")]
            closed = list(buf.closed)[-24:]
            if not closed:
                continue
            op = closed[0].o
            hi = max([c.h for c in closed] + [st.price])
            lo = min([c.l for c in closed] + [st.price])
            qv = sum(c.qv for c in closed)
            self.tickers[sym] = Ticker(
                last=st.price, open=op, high=hi, low=lo, quote_volume=qv,
                change_pct=(st.price / op - 1.0) * 100.0, ts=ts,
            )

    def _refresh_marks(self) -> None:
        ts = now_ms()
        nxt = ts - (ts % (8 * 3600_000)) + 8 * 3600_000
        for sym, st in self._state.items():
            mark = st.price * (1.0 + 0.00005 * float(self.rng.standard_normal()))
            self.marks[sym] = MarkInfo(mark=mark, index=st.price, funding_rate=st.funding, next_funding_time=nxt, ts=ts)

    # --- canlı akış ----------------------------------------------------
    async def start(self) -> None:
        self._stopping = False
        await asyncio.to_thread(self._generate)
        self.state = "ok"
        self.state_message = "Sentetik geliştirme verisi (gerçek piyasa değil)"
        self.last_msg_ts = now_ms()
        self.ready.set()
        self._task = asyncio.create_task(self._run(), name="synthetic-feed")

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        counter = 0
        while not self._stopping:
            await asyncio.sleep(self.tick_interval)
            self._tick()
            counter += 1
            if counter % 5 == 0:
                self._refresh_tickers()

    def _tick(self) -> None:
        rng = self.rng
        ts = now_ms()
        dt = self.tick_interval / 60.0
        btc = float(rng.standard_normal())
        closed_events: list[tuple[str, str, Candle]] = []
        for sym, st in self._state.items():
            if sym == "BTCUSDT":
                r = st.drift * dt + st.sigma * math.sqrt(dt) * btc
            else:
                r = st.drift * dt + st.beta * 0.0008 * 0.6 * math.sqrt(dt) * btc + st.sigma * 0.8 * math.sqrt(dt) * float(rng.standard_normal())
            st.price *= math.exp(r)
            qv_tick = st.qv_min * dt * math.exp(0.3 * float(rng.standard_normal()))
            taker = min(0.85, max(0.15, 0.5 + 0.15 * r / (st.sigma * math.sqrt(dt)) * 0.3))
            for tf in TFS:
                step = TF_MS[tf]
                cur = st.cur[tf]
                buf = self.buffers[(sym, tf)]
                if ts >= cur.t + step:
                    cur.closed = True
                    closed = buf.update(Candle(cur.t, cur.o, cur.h, cur.l, cur.c, cur.v, cur.qv, cur.tbq, True))
                    self._emit_kline(sym, tf, cur)
                    if closed is not None:
                        closed_events.append((sym, tf, closed))
                    bt = ts - (ts % step)
                    cur = Candle(t=bt, o=st.price, h=st.price, l=st.price, c=st.price, v=0.0, qv=0.0, tbq=0.0, closed=False)
                    st.cur[tf] = cur
                cur.h = max(cur.h, st.price)
                cur.l = min(cur.l, st.price)
                cur.c = st.price
                cur.qv += qv_tick
                cur.v += qv_tick / st.price
                cur.tbq += qv_tick * taker
                buf.update(Candle(cur.t, cur.o, cur.h, cur.l, cur.c, cur.v, cur.qv, cur.tbq, False))
                self._emit_kline(sym, tf, cur)
            t = self.tickers.get(sym)
            if t:
                t.last = st.price
                t.ts = ts
        self._refresh_marks()
        self.last_msg_ts = ts
        loop = asyncio.get_running_loop()
        for sym, tf, c in closed_events:
            loop.create_task(self._emit_bar_close(sym, tf, c))

    # --- sorgular ------------------------------------------------------
    async def klines(self, symbol: str, tf: str, limit: int = 300) -> list[Candle]:
        buf = self.buffers.get((symbol, tf))
        if buf is None:
            return []
        return buf.chart(limit)

    async def watch(self, symbol: str, tf: str) -> None:
        return None

    async def unwatch(self, symbol: str, tf: str) -> None:
        return None

    async def book(self, symbol: str) -> Optional[tuple[float, float]]:
        st = self._state.get(symbol)
        if not st:
            return None
        return st.price * (1 - 0.00005), st.price * (1 + 0.00005)

    async def oi_change_pct(self, symbol: str, minutes: int = 60) -> Optional[float]:
        scen = self._scenario.get(symbol)
        if scen in ("breakout_long", "breakdown_short"):
            return 3.4
        return float(self.rng.normal(0.3, 1.0))
