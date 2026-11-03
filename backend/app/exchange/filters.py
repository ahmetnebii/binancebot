"""Borsa filtreleri: fiyat/miktar yuvarlama (Decimal) ve doğrulama."""
from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal
from typing import Literal, Optional

from ..models import SymbolInfo


def D(x: object) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def floor_step(value: object, step: object) -> Decimal:
    v, s = D(value), D(step)
    if s <= 0:
        return v
    return (v / s).to_integral_value(rounding=ROUND_DOWN) * s


def round_tick(value: object, tick: object, mode: Literal["down", "up", "nearest"] = "nearest") -> Decimal:
    v, t = D(value), D(tick)
    if t <= 0:
        return v
    rounding = {"down": ROUND_DOWN, "up": ROUND_UP, "nearest": ROUND_HALF_UP}[mode]
    return (v / t).to_integral_value(rounding=rounding) * t


def fmt(d: Decimal) -> str:
    s = format(d.normalize(), "f")
    return "0" if s in ("-0", "-0.0") else s


def stop_price(side: str, raw: float, tick: str) -> Decimal:
    """Stop'u girişe doğru yuvarlar: gerçekleşen risk planlanandan büyük olmaz."""
    return round_tick(raw, tick, "up" if side == "LONG" else "down")


def tp_price(side: str, raw: float, tick: str) -> Decimal:
    """Hedefi girişe doğru yuvarlar: dolma ihtimali artar."""
    return round_tick(raw, tick, "down" if side == "LONG" else "up")


def check_qty(sym: SymbolInfo, qty: Decimal, price: float, market: bool = True, reduce_only: bool = False) -> Optional[str]:
    if qty <= 0 or qty < D(sym.min_qty):
        return f"Miktar borsanın minimumunun ({sym.min_qty}) altında"
    max_q = D(sym.market_max_qty if market else sym.max_qty)
    if qty > max_q:
        return f"Miktar borsanın maksimumunu ({sym.market_max_qty}) aşıyor"
    if not reduce_only and qty * D(price) < D(sym.min_notional):
        return f"Pozisyon değeri borsanın minimumunun ({sym.min_notional} USDT) altında"
    return None
