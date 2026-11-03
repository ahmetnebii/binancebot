"""Sinyal için tahmini kazanma olasılığı ve beklenti (EV, R cinsinden).

Öncelik sırası:
1. Varsayılan strateji istatistikleri (PRIORS)
2. Backtest kalibrasyonu uygulanmışsa: ölçülmüş istatistikler (işlem sayısına göre ağırlıklı)
3. Canlı/simülasyon sonuçları biriktikçe bunlarla harmanlanır
Az veride tahmin önceki bilgiye doğru çekilir (aşırı iyimserliği önler).
"""
from __future__ import annotations

from typing import Optional

from ..models import Signal, side_sign

# (kazanma oranı, ortalama kazanç R, ortalama kayıp R)
PRIORS: dict[str, tuple[float, float, float]] = {
    "S1": (0.40, 2.1, 1.0),
    "S2": (0.45, 1.8, 1.0),
    "S3": (0.55, 1.0, 1.0),
    "S4": (0.38, 2.6, 1.0),
    "S5": (0.45, 1.8, 1.0),
    "M": (0.40, 2.0, 1.0),
}
PRIOR_WEIGHT = 30
MAX_CALIBRATION_WEIGHT = 150


def _blend(a: float, wa: float, b: float, wb: float) -> float:
    return (a * wa + b * wb) / (wa + wb) if (wa + wb) > 0 else a


def prior_for(strategy: str, calibration: Optional[dict] = None) -> tuple[float, float, float, float]:
    """(p, ortalama kazanç R, ortalama kayıp R, ağırlık)"""
    p0, w0, l0 = PRIORS.get(strategy, (0.40, 2.0, 1.0))
    weight = float(PRIOR_WEIGHT)
    cal = (calibration or {}).get(strategy)
    if cal and cal.get("n", 0) >= 10:
        n = float(min(cal["n"], MAX_CALIBRATION_WEIGHT))
        p0 = _blend(p0, PRIOR_WEIGHT, float(cal["win_rate"]), n)
        if cal.get("avg_win_r"):
            w0 = _blend(w0, PRIOR_WEIGHT, float(cal["avg_win_r"]), n)
        if cal.get("avg_loss_r"):
            l0 = _blend(l0, PRIOR_WEIGHT, float(cal["avg_loss_r"]), n)
        weight += n
    return p0, w0, l0, weight


def estimate(
    sig: Signal,
    taker_fee: float,
    slippage: float,
    funding_rate: float,
    stats: Optional[dict] = None,
    calibration: Optional[dict] = None,
) -> tuple[float, float]:
    p0, w0, l0, weight = prior_for(sig.strategy, calibration)
    if stats and stats.get("n", 0) > 0:
        n = float(stats["n"])
        p0 = _blend(p0, weight, float(stats["win_rate"]), n)
        if stats.get("avg_win_r"):
            w0 = _blend(w0, weight, float(stats["avg_win_r"]), n)
        if stats.get("avg_loss_r"):
            l0 = _blend(l0, weight, float(stats["avg_loss_r"]), n)
    p = p0 + (sig.score - 70.0) / 100.0 * 0.15
    p = max(0.15, min(0.80, p))
    if sig.take_profits and not sig.trailing_atr_mult:
        # Tek hedefli stratejide ortalama kazanç, hedefin R katıyla sınırlıdır
        w0 = min(w0, max(t.r for t in sig.take_profits))
    d = abs(sig.entry - sig.stop) / sig.entry if sig.entry > 0 else 0.0
    if d <= 0:
        return p, -1.0
    cost_r = (2 * taker_fee + slippage) / d
    funding_r = max(0.0, side_sign(sig.side) * funding_rate) / d
    ev = p * w0 - (1.0 - p) * l0 - cost_r - funding_r
    return round(p, 4), round(ev, 4)
