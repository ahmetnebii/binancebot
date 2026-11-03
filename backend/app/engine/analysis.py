"""Sembol için çok zaman dilimli analiz: trend, momentum, oynaklık, türev verisi, likidite.

Fırsat ve pozisyon ekranlarındaki "Analiz" panelini besler; ayrıca strateji puanlarına
ek teyit (4 saatlik trend uyumu) sağlar.
"""
from __future__ import annotations

import math
import time
from typing import Optional

import numpy as np

from ..features.indicators import (
    adx,
    aggregate_bars,
    atr,
    beta,
    bollinger,
    correlation,
    ema,
    log_returns,
    log_volume_z,
    rsi,
)
from ..features.mtf import four_hour_trend, trend_of  # noqa: F401
from ..features.regime import Regime
from ..market.base import MarketData
from ..models import Candle
from ..util import TF_MS, now_ms

TREND_TR = {"up": "Yükseliş", "down": "Düşüş", "flat": "Yatay"}
_cache: dict[str, tuple[float, dict]] = {}


def _f(x: float) -> Optional[float]:
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    return xf if math.isfinite(xf) else None


def arrays_from_candles(candles: list[Candle]) -> dict[str, np.ndarray]:
    cs = [c for c in candles if c.closed]
    return {
        "t": np.array([c.t for c in cs], dtype=np.int64),
        "o": np.array([c.o for c in cs], dtype=float),
        "h": np.array([c.h for c in cs], dtype=float),
        "l": np.array([c.l for c in cs], dtype=float),
        "c": np.array([c.c for c in cs], dtype=float),
        "v": np.array([c.v for c in cs], dtype=float),
        "qv": np.array([c.qv for c in cs], dtype=float),
        "tbq": np.array([c.tbq for c in cs], dtype=float),
    }


def tf_analysis(a: dict[str, np.ndarray], tf: str) -> Optional[dict]:
    c, h, l = a["c"], a["h"], a["l"]  # noqa: E741
    if len(c) < 30:
        return None
    c, h, l, qv = c[-500:], h[-500:], l[-500:], a["qv"][-500:]
    trend, stack, e20, _, _ = trend_of(c)
    price = float(c[-1])
    at = _f(atr(h, l, c, 14)[-1])
    _, up, lo, _ = bollinger(c, 20, 2.0)
    band = _f(up[-1] - lo[-1])
    pctb = (price - float(lo[-1])) / band if band else None
    return {
        "tf": tf,
        "trend": trend,
        "ema_stack": stack,
        "rsi": _f(rsi(c, 14)[-1]),
        "adx": _f(adx(h, l, c, 14)[-1]),
        "atr_pct": (at / price * 100.0) if at and price else None,
        "bb_pctb": _f(pctb) if pctb is not None else None,
        "dist_ema20_atr": ((price - e20) / at) if at and not math.isnan(e20) else None,
        "vol_z": _f(log_volume_z(qv, 96)) if len(qv) > 97 else None,
        "change_pct": ((price / float(c[-25]) - 1.0) * 100.0) if len(c) > 25 else None,
    }


async def _arrays(market: MarketData, symbol: str, tf: str, limit: int = 500) -> Optional[dict[str, np.ndarray]]:
    buf = market.buffer(symbol, tf)
    if buf is not None and len(buf) >= 60:
        return buf.arrays()
    try:
        candles = await market.klines(symbol, tf, limit)
    except Exception:
        return None
    a = arrays_from_candles(candles)
    return a if len(a["c"]) >= 30 else None


async def symbol_analysis(market: MarketData, symbol: str, regime: Regime, max_spread_bps: float) -> dict:
    key = symbol
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < 10.0:
        return hit[1]

    a15 = await _arrays(market, symbol, "15m")
    a1h = await _arrays(market, symbol, "1h")
    tfs: list[dict] = []
    if a15 is not None:
        r = tf_analysis(a15, "15m")
        if r:
            tfs.append(r)
    if a1h is not None:
        r = tf_analysis(a1h, "1h")
        if r:
            tfs.append(r)
        a4 = aggregate_bars(a1h, TF_MS["4h"])
        r4 = tf_analysis(a4, "4h") if len(a4["c"]) >= 30 else None
        if r4:
            tfs.append(r4)

    ups = sum(1 for t in tfs if t["trend"] == "up")
    downs = sum(1 for t in tfs if t["trend"] == "down")
    n = len(tfs)
    if n and ups == n:
        label = f"{ups}/{n} zaman dilimi yükselişte"
    elif n and downs == n:
        label = f"{downs}/{n} zaman dilimi düşüşte"
    else:
        flats = n - ups - downs
        label = f"Karışık: {ups} yükseliş, {downs} düşüş, {flats} yatay"

    price = market.price(symbol)
    mi = market.funding(symbol)
    oi = await market.oi_change_pct(symbol, 60)
    liq_long, liq_short = market.liquidation_sums(symbol, 3600_000)
    t = market.tickers.get(symbol)

    btc_corr = btc_beta = rs = None
    if symbol != "BTCUSDT" and a1h is not None:
        bb = await _arrays(market, "BTCUSDT", "1h")
        if bb is not None and len(bb["c"]) > 80 and len(a1h["c"]) > 80:
            ra = log_returns(a1h["c"][-73:])
            rb = log_returns(bb["c"][-73:])
            btc_corr = _f(correlation(ra, rb))
            btc_beta = _f(beta(ra, rb))
            rs = ((a1h["c"][-1] / a1h["c"][-25] - 1.0) - (bb["c"][-1] / bb["c"][-25] - 1.0)) * 100.0

    bid = ask = spread = None
    book = await market.book(symbol)
    if book:
        bid, ask = book
        mid = (bid + ask) / 2.0
        spread = (ask - bid) / mid * 10_000 if mid > 0 else None

    notes: list[str] = []
    if n:
        notes.append(f"Zaman dilimi uyumu: {label.lower()}")
    by_tf = {x["tf"]: x for x in tfs}
    h1 = by_tf.get("1h")
    if h1 and h1["rsi"] is not None:
        if h1["rsi"] >= 70:
            notes.append(f"1 saatlik RSI {h1['rsi']:.0f}: aşırı alım bölgesi, geri çekilme riski")
        elif h1["rsi"] <= 30:
            notes.append(f"1 saatlik RSI {h1['rsi']:.0f}: aşırı satım bölgesi, tepki riski")
    if h1 and h1["adx"] is not None:
        notes.append(
            f"1 saatlik ADX {h1['adx']:.0f}: " + ("güçlü trend" if h1["adx"] >= 25 else "zayıf trend / yatay seyir")
        )
    if h1 and h1["atr_pct"] is not None and h1["atr_pct"] >= 2.0:
        notes.append(f"Oynaklık yüksek (1 saatlik ATR %{h1['atr_pct']:.1f}): stoplar geniş, pozisyonlar küçük olur")
    if mi is not None:
        fr = mi.funding_rate
        if fr >= 0.0003:
            notes.append(f"Funding %{fr * 100:+.3f}: long'lar kalabalık, long taşımak maliyetli")
        elif fr <= -0.0003:
            notes.append(f"Funding %{fr * 100:+.3f}: short'lar kalabalık, short sıkışması riski")
    if oi is not None and abs(oi) >= 1.0:
        notes.append(
            f"Open interest son 1 saatte %{oi:+.1f} " + ("arttı: yeni pozisyon girişi var" if oi > 0 else "azaldı: pozisyonlar kapanıyor")
        )
    if liq_long is not None and liq_short is not None and (liq_long + liq_short) > 0:
        notes.append(f"Son 1 saatte likidasyon: long {liq_long:,.0f} USDT, short {liq_short:,.0f} USDT")
    if spread is not None:
        notes.append(
            f"Spread {spread:.1f} bps: " + ("likidite iyi" if spread <= max_spread_bps else "yüksek, kayma riski — işlem engellenir")
        )
    if btc_corr is not None and btc_corr >= 0.8:
        notes.append(f"BTC ile korelasyon yüksek ({btc_corr:.2f}): BTC hareketlerine çok duyarlı")

    out = {
        "symbol": symbol,
        "price": price,
        "updated_at": now_ms(),
        "timeframes": tfs,
        "confluence": {"long": ups, "short": downs, "label": label},
        "derivatives": {
            "funding_rate": mi.funding_rate if mi else None,
            "next_funding_time": mi.next_funding_time if mi and mi.next_funding_time else None,
            "oi_change_1h_pct": oi,
            "liq_long_1h": liq_long,
            "liq_short_1h": liq_short,
        },
        "market": {
            "change_24h_pct": t.change_pct if t else None,
            "quote_volume_24h": t.quote_volume if t else None,
            "volume_rank": market.volume_rank(symbol) if t else None,
            "btc_corr": btc_corr,
            "btc_beta": btc_beta,
            "rs_24h_pct": _f(rs) if rs is not None else None,
        },
        "liquidity": {"bid": bid, "ask": ask, "spread_bps": spread},
        "regime": regime.model_dump(),
        "notes": notes,
    }
    _cache[key] = (time.monotonic(), out)
    if len(_cache) > 300:
        _cache.pop(next(iter(_cache)))
    return out
