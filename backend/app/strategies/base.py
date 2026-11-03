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


def side_allowed(strategy: str, side: str, settings: StrategySettings) -> bool:
    """Strateji için izin verilen yön (both / long / short)."""
    allowed = getattr(settings, f"{strategy.lower()}_sides", "both")
    return allowed == "both" or allowed == side.lower()


TREND_STRATEGIES = ("S1", "S2", "S4", "S5")


def passes_filters(sig: Signal, settings: StrategySettings, regime: str, funding_rate: float) -> bool:
    """Tüm stratejilere ortak son süzgeç (canlı tarama ve backtest aynı kuralı kullanır).

    - Yön izni (sN_sides)
    - Sıkı rejim filtresi: trend stratejileri yalnızca rejim yönünde (boğada long, ayıda short),
      ortalamaya dönüş yalnızca yatay piyasada işlem açar
    - Kalabalık pozisyon filtresi: kendi yönümüze ödenen funding eşiği aşıyorsa girme
    """
    if not side_allowed(sig.strategy, sig.side, settings):
        return False
    if getattr(settings, "regime_filter", "normal") == "strict":
        if sig.strategy in TREND_STRATEGIES:
            if not ((regime == "BULL" and sig.side == "LONG") or (regime == "BEAR" and sig.side == "SHORT")):
                return False
        elif sig.strategy == "S3" and regime != "RANGE":
            return False
    fmax = float(getattr(settings, "funding_max", 0.0005))
    if side_sign(sig.side) * funding_rate > fmax:
        return False
    return True


EXIT_PROFILES = {
    # (hedef merdiveni, iz süren stop çarpanı katsayısı, zaman stopu katsayısı)
    "runner": ([(2.0, 0.25)], 1.25, 2.0),   # küçük kâr al, kalanı trende bırak
    "quick": ([(1.0, 0.5), (2.0, 0.5)], None, 1.0),  # hızlı kâr realize et, iz süren stop yok
}


def apply_exit_profile(sig: Signal, settings: StrategySettings) -> Signal:
    """Çıkış profilini sinyalin hedef/iz süren stop planına uygular (standard: dokunmaz)."""
    prof = EXIT_PROFILES.get(getattr(settings, "exit_profile", "standard"))
    if prof is None or sig.strategy == "S3":  # ortalamaya dönüş kendi tek hedefini korur
        return sig
    spec, trail_k, time_k = prof
    sgn = side_sign(sig.side)
    risk = abs(sig.entry - sig.stop)
    sig.take_profits = [TakeProfit(r=r, price=sig.entry + sgn * r * risk, fraction=f) for r, f in spec]
    sig.rr = max(r for r, _ in spec)
    if trail_k is None:
        sig.trailing_atr_mult = None
    elif sig.trailing_atr_mult:
        sig.trailing_atr_mult = round(sig.trailing_atr_mult * trail_k, 2)
    if sig.time_stop_bars:
        sig.time_stop_bars = int(sig.time_stop_bars * time_k)
    return sig


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
