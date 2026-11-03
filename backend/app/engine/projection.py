"""Açık pozisyon için tahmini ilerleme (projeksiyon).

Söz değil, istatistiksel tahmindir:
- Volatilite konisi: log-getirilerin bar başına standart sapmasıyla ±1σ ve ±2σ aralığı.
- Hedefe stop'tan önce ulaşma olasılığı (yönsüz model): log-fiyat sürüklenmesiz
  Brown hareketi varsayımıyla p = (x - a) / (b - a).
- İvme varsayımıyla olasılık: son barların ortalama getirisi sürüklenme kabul edilir.
- Beklenen sonuçlanma süresi (yönsüz): E[T] = (x - a)(b - x) / σ².
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ..models import Position
from ..util import tf_ms


def _hit_upper_prob(x: float, lo: float, hi: float, mu: float, var: float) -> float:
    """Sürüklenmeli Brown hareketinde üst bariyere alt bariyerden önce ulaşma olasılığı."""
    if hi <= lo:
        return 0.5
    if x <= lo:
        return 0.0
    if x >= hi:
        return 1.0
    if abs(mu) < 1e-12 or var <= 0:
        return (x - lo) / (hi - lo)
    k = -2.0 * mu / var
    try:
        num = 1.0 - math.exp(k * (x - lo))
        den = 1.0 - math.exp(k * (hi - lo))
    except OverflowError:
        return 1.0 if mu > 0 else 0.0
    if den == 0:
        return (x - lo) / (hi - lo)
    return max(0.0, min(1.0, num / den))


def build_projection(pos: Position, arrays: dict[str, np.ndarray], tf: str, mark: float, horizon: int = 32) -> Optional[dict]:
    c = arrays.get("c")
    t = arrays.get("t")
    if c is None or t is None or len(c) < 30 or mark <= 0:
        return None
    r = np.diff(np.log(c[-97:]))
    sigma = float(r.std()) if len(r) > 5 else 0.0
    if not math.isfinite(sigma) or sigma <= 0:
        sigma = 1e-4
    mu = float(np.mean(r[-12:])) if len(r) >= 12 else 0.0
    step_s = tf_ms(tf) // 1000
    cur_t = int(t[-1] // 1000) + step_s  # oluşmakta olan barın zamanı

    cone = []
    path = []
    for k in range(0, horizon + 1):
        s = sigma * math.sqrt(k)
        cone.append({
            "time": cur_t + k * step_s,
            "up1": mark * math.exp(s),
            "dn1": mark * math.exp(-s),
            "up2": mark * math.exp(2 * s),
            "dn2": mark * math.exp(-2 * s),
        })
        path.append({"time": cur_t + k * step_s, "price": mark * math.exp(mu * k)})

    target_price: Optional[float] = None
    target_label: Optional[str] = None
    for i, tp in enumerate(pos.take_profits, start=1):
        if not tp.filled:
            target_price = tp.price
            target_label = f"TP{i}"
            break

    p_flat = p_mom = exp_bars = eta = None
    stop = pos.stop_price
    if target_price and stop > 0 and target_price > 0:
        x = math.log(mark)
        a = math.log(stop)
        b = math.log(target_price)
        lo, hi = min(a, b), max(a, b)
        var = sigma * sigma
        up_flat = _hit_upper_prob(x, lo, hi, 0.0, var)
        up_mom = _hit_upper_prob(x, lo, hi, mu, var)
        target_is_upper = b > a
        p_flat = up_flat if target_is_upper else 1.0 - up_flat
        p_mom = up_mom if target_is_upper else 1.0 - up_mom
        if lo < x < hi and var > 0:
            eb = (x - lo) * (hi - x) / var
            exp_bars = eb if eb < 100_000 else None
        direction = 1 if target_is_upper else -1
        if direction * mu > 1e-9:
            e = (b - x) / mu
            eta = e if 0 < e < 100_000 else None

    return {
        "timeframe": tf,
        "sigma_bar": sigma,
        "horizon_bars": horizon,
        "target_price": target_price,
        "target_label": target_label,
        "p_target_first": None if p_flat is None else round(p_flat, 4),
        "p_target_first_momentum": None if p_mom is None else round(p_mom, 4),
        "expected_bars": None if exp_bars is None else round(exp_bars, 1),
        "eta_bars_momentum": None if eta is None else round(eta, 1),
        "cone": cone,
        "momentum_path": path,
    }
