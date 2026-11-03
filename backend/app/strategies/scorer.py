"""Sinyal için tahmini kazanma olasılığı ve beklenti (EV, R cinsinden).

Başlangıçta strateji başına varsayılan istatistikler kullanılır; kapanan işlemler
biriktikçe gerçek sonuçlarla harmanlanır (az veride varsayılana doğru çekilir).
"""
from __future__ import annotations

from typing import Optional

from ..models import Signal, side_sign

PRIORS: dict[str, tuple[float, float]] = {
    "S1": (0.40, 2.1),  # (kazanma oranı, ortalama kazanç R)
    "S2": (0.45, 1.8),
    "M": (0.40, 2.0),
}
PRIOR_WEIGHT = 30


def estimate(
    sig: Signal,
    taker_fee: float,
    slippage: float,
    funding_rate: float,
    stats: Optional[dict] = None,
) -> tuple[float, float]:
    p0, w0 = PRIORS.get(sig.strategy, (0.40, 2.0))
    if stats and stats.get("n", 0) > 0:
        n = stats["n"]
        p0 = (n * stats["win_rate"] + PRIOR_WEIGHT * p0) / (n + PRIOR_WEIGHT)
        if stats.get("avg_win_r"):
            w0 = (n * stats["avg_win_r"] + PRIOR_WEIGHT * w0) / (n + PRIOR_WEIGHT)
    p = p0 + (sig.score - 70.0) / 100.0 * 0.15
    p = max(0.15, min(0.75, p))
    d = abs(sig.entry - sig.stop) / sig.entry if sig.entry > 0 else 0.0
    if d <= 0:
        return p, -1.0
    cost_r = (2 * taker_fee + slippage) / d
    funding_r = max(0.0, side_sign(sig.side) * funding_rate) / d
    ev = p * w0 - (1.0 - p) * 1.0 - cost_r - funding_r
    return round(p, 4), round(ev, 4)
