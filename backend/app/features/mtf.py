"""Çok zaman dilimli (MTF) trend yardımcıları."""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ..util import TF_MS
from .indicators import aggregate_bars, ema


def trend_of(c: np.ndarray) -> tuple[str, str, float, float, float]:
    """(trend, EMA dizilimi metni, ema20, ema50, ema200).

    up: fiyat > EMA50, EMA20 > EMA50 ve (varsa) EMA50 > EMA200; down simetriği; aksi hâlde flat.
    """
    price = float(c[-1])
    e20 = float(ema(c, 20)[-1]) if len(c) >= 20 else float("nan")
    e50 = float(ema(c, 50)[-1]) if len(c) >= 50 else float("nan")
    e200 = float(ema(c, 200)[-1]) if len(c) >= 200 else float("nan")
    trend = "flat"
    if not math.isnan(e20) and not math.isnan(e50):
        long_ok = math.isnan(e200) or e50 > e200
        short_ok = math.isnan(e200) or e50 < e200
        if price > e50 and e20 > e50 and long_ok:
            trend = "up"
        elif price < e50 and e20 < e50 and short_ok:
            trend = "down"
    items = [("Fiyat", price), ("EMA20", e20), ("EMA50", e50), ("EMA200", e200)]
    items = [(n, v) for n, v in items if not math.isnan(v)]
    stack = " > ".join(n for n, _ in sorted(items, key=lambda x: x[1], reverse=True))
    return trend, stack, e20, e50, e200


def four_hour_trend(market, symbol: str) -> Optional[str]:
    """1 saatlik tampondan 4 saatlik trend (ağ isteği yapmaz). Veri yetersizse None."""
    buf = market.buffer(symbol, "1h")
    if buf is None or len(buf) < 120:
        return None
    a4 = aggregate_bars(buf.arrays(), TF_MS["4h"])
    if len(a4["c"]) < 30:
        return None
    return trend_of(a4["c"])[0]
