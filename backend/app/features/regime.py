"""Piyasa rejimi: BTC trendi, genişlik (breadth) ve oynaklığa göre."""
from __future__ import annotations

from typing import Optional

import numpy as np
from pydantic import BaseModel

from ..market.base import MarketData
from ..util import now_ms
from .indicators import atr, ema, pct_rank_last

LABELS = {
    "BULL": "Boğa trendi",
    "BEAR": "Ayı trendi",
    "RANGE": "Yatay piyasa",
    "CHAOS": "Aşırı oynaklık",
    "UNKNOWN": "Belirleniyor",
}
RISK_MULT = {"BULL": 1.0, "BEAR": 1.0, "RANGE": 0.75, "CHAOS": 0.0, "UNKNOWN": 0.5}


class Regime(BaseModel):
    name: str = "UNKNOWN"
    label: str = LABELS["UNKNOWN"]
    risk_mult: float = RISK_MULT["UNKNOWN"]
    btc_trend: str = "—"
    breadth: Optional[float] = None
    vol_pct: Optional[float] = None
    updated_at: Optional[int] = None


class RegimeDetector:
    def __init__(self) -> None:
        self.current = Regime()
        self._pending: Optional[str] = None
        self._pending_count = 0

    def raw(self, market: MarketData) -> Optional[Regime]:
        buf = market.buffer("BTCUSDT", "1h")
        if buf is None or len(buf) < 210:
            return None
        a = buf.arrays()
        c, h, l = a["c"], a["h"], a["l"]  # noqa: E741
        e50 = ema(c, 50)
        e200 = ema(c, 200)
        slope = (e50[-1] - e50[-11]) / e50[-11] if not np.isnan(e50[-11]) else 0.0
        at = atr(h, l, c, 14)
        vol_pct = pct_rank_last(at / c, 200)
        ret_1h = abs(c[-1] / c[-2] - 1.0)
        above = 0
        total = 0
        for sym in market.universe:
            b = market.buffer(sym, "1h")
            if b is None or len(b) < 60:
                continue
            cc = b.arrays()["c"]
            e = ema(cc, 50)
            if np.isnan(e[-1]):
                continue
            total += 1
            above += int(cc[-1] > e[-1])
        breadth = above / total if total else None
        if e50[-1] > e200[-1] and slope > 0:
            btc_trend = "Yükseliş (EMA50 > EMA200)"
        elif e50[-1] < e200[-1] and slope < 0:
            btc_trend = "Düşüş (EMA50 < EMA200)"
        else:
            btc_trend = "Kararsız"
        if ret_1h > 0.03 or vol_pct > 0.95:
            name = "CHAOS"
        elif e50[-1] > e200[-1] and slope > 0 and (breadth is None or breadth > 0.55):
            name = "BULL"
        elif e50[-1] < e200[-1] and slope < 0 and (breadth is None or breadth < 0.45):
            name = "BEAR"
        else:
            name = "RANGE"
        return Regime(
            name=name, label=LABELS[name], risk_mult=RISK_MULT[name], btc_trend=btc_trend,
            breadth=breadth, vol_pct=vol_pct, updated_at=now_ms(),
        )

    def update(self, market: MarketData) -> Regime:
        r = self.raw(market)
        if r is None:
            return self.current
        if self.current.name in ("UNKNOWN",) or r.name == "CHAOS" or r.name == self.current.name:
            # ilk tespit, kaos ve aynı rejim anında uygulanır
            self.current = r
            self._pending = None
            self._pending_count = 0
            return self.current
        # histerezis: yeni rejim için 2 ardışık onay
        if self._pending == r.name:
            self._pending_count += 1
        else:
            self._pending = r.name
            self._pending_count = 1
        if self._pending_count >= 2:
            self.current = r
            self._pending = None
            self._pending_count = 0
        else:
            self.current = self.current.model_copy(update={"breadth": r.breadth, "vol_pct": r.vol_pct, "updated_at": r.updated_at})
        return self.current
