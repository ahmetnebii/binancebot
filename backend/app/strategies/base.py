"""Strateji altyapısı: bağlam ve sinyal üretimi."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..features.regime import Regime
from ..market.base import MarketData
from ..models import Signal, TakeProfit, side_sign
from ..settings import ScannerSettings, StrategySettings
from ..util import new_id


@dataclass
class StrategyContext:
    market: MarketData
    regime: Regime
    settings: StrategySettings
    scanner: ScannerSettings
    now: int

    def arrays(self, symbol: str, tf: str, min_len: int) -> Optional[dict[str, np.ndarray]]:
        buf = self.market.buffer(symbol, tf)
        if buf is None or len(buf) < min_len:
            return None
        return buf.arrays()

    def funding_rate(self, symbol: str) -> float:
        mi = self.market.funding(symbol)
        return mi.funding_rate if mi else 0.0


def clamp01(x: float) -> float:
    if x != x:  # NaN
        return 0.0
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def make_signal(
    *,
    symbol: str,
    side: str,
    strategy: str,
    strategy_name: str,
    timeframe: str,
    bar_time: int,
    now: int,
    ttl_ms: int,
    entry: float,
    stop: float,
    tp_spec: list[tuple[float, float]],
    atr_value: float,
    score: float,
    reasons: list[str],
    warnings: list[str],
    features: dict[str, float],
    trailing_atr_mult: Optional[float],
    time_stop_bars: int,
) -> Signal:
    sgn = side_sign(side)
    risk = abs(entry - stop)
    tps = [TakeProfit(r=r, price=entry + sgn * r * risk, fraction=f) for r, f in tp_spec]
    return Signal(
        id=new_id("sig"),
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        strategy=strategy,
        strategy_name=strategy_name,
        timeframe=timeframe,
        created_at=now,
        expires_at=now + ttl_ms,
        bar_time=bar_time,
        entry=entry,
        stop=stop,
        take_profits=tps,
        trailing_atr_mult=trailing_atr_mult,
        time_stop_bars=time_stop_bars,
        atr=atr_value,
        score=round(max(0.0, min(100.0, score)), 1),
        rr=max((r for r, _ in tp_spec), default=0.0),
        reasons=reasons,
        warnings=warnings,
        features={k: round(float(v), 6) for k, v in features.items() if v == v},
    )
