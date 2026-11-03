"""Pozisyon büyüklüğü ve kaldıraç önerisi (bkz. YAPIM_KILAVUZU.md Bölüm 10.2)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SizingInput:
    equity: float  # USDT özsermaye (cüzdan + gerçekleşmemiş K/Z)
    entry: float
    stop: float
    risk_pct: float  # çarpanlar uygulanmış işlem riski (0.0075 = %0,75)
    taker_fee: float
    slippage: float
    max_margin_pct: float
    mmr: float  # bakım marjin oranı
    class_cap: int
    bracket_cap: int
    global_cap: int = 20
    liq_buffer_k: float = 3.0
    leverage_override: Optional[int] = None


@dataclass(frozen=True)
class SizingResult:
    notional: float
    leverage: int
    margin: float
    risk_usdt: float
    stop_distance: float
    cost: float
    lev_liq: int
    lev_cap: int
    reduced: bool = False


def max_safe_leverage(stop_distance: float, mmr: float, k: float) -> int:
    """Likidasyon mesafesi ≥ k × stop mesafesi olacak en yüksek kaldıraç."""
    return int(math.floor(1.0 / (k * stop_distance + mmr)))


def size_and_leverage(p: SizingInput) -> Optional[SizingResult]:
    if p.entry <= 0 or p.equity <= 0:
        return None
    d = abs(p.entry - p.stop) / p.entry
    if d <= 0:
        return None
    cost = 2 * p.taker_fee + p.slippage
    notional = p.equity * p.risk_pct / (d + cost)

    lev_liq = max_safe_leverage(d, p.mmr, p.liq_buffer_k)
    lev_cap = min(lev_liq, p.class_cap, p.bracket_cap, p.global_cap)
    if lev_cap < 1:
        return None

    max_margin = p.equity * p.max_margin_pct
    if p.leverage_override is not None:
        leverage = max(1, min(int(p.leverage_override), lev_cap))
    else:
        leverage = max(1, math.ceil(notional / max_margin))
    reduced = False
    if leverage > lev_cap:
        leverage = lev_cap
    if notional / leverage > max_margin:
        notional = max_margin * leverage
        reduced = True
    return SizingResult(
        notional=notional,
        leverage=leverage,
        margin=notional / leverage,
        risk_usdt=notional * (d + cost),
        stop_distance=d,
        cost=cost,
        lev_liq=lev_liq,
        lev_cap=lev_cap,
        reduced=reduced,
    )


def approx_liq_price(side: str, entry: float, leverage: int, mmr: float) -> float:
    """İzole marjin, tek yönlü mod için yaklaşık likidasyon fiyatı."""
    if leverage <= 0:
        return 0.0
    if side == "LONG":
        return max(0.0, entry * (1.0 - 1.0 / leverage + mmr))
    return entry * (1.0 + 1.0 / leverage - mmr)


def symbol_class(symbol: str, volume_rank: int) -> str:
    if symbol in ("BTCUSDT", "ETHUSDT") or volume_rank <= 2:
        return "major"
    if volume_rank <= 20:
        return "large"
    return "other"
