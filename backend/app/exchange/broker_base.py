"""Broker arayüzü: simülasyon (paper) ve Binance (testnet/canlı) aynı sözleşmeyi uygular."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Awaitable, Callable, Literal, Optional


class BrokerError(Exception):
    """Kullanıcıya gösterilebilir broker hatası (Türkçe mesaj)."""


@dataclass
class AccountInfo:
    equity: float
    wallet: float
    unrealized: float
    available: float
    margin_used: float


@dataclass
class TPOrder:
    index: int  # 1 tabanlı
    price: Decimal
    qty: Decimal


@dataclass
class OpenRequest:
    position_id: str
    symbol: str
    side: Literal["LONG", "SHORT"]
    qty: Decimal
    leverage: int
    ref_price: float
    stop_price: Decimal
    tps: list[TPOrder] = field(default_factory=list)
    entry_mode: Literal["market", "ioc"] = "ioc"
    max_slippage_pct: float = 0.002
    mmr: float = 0.005


@dataclass
class Fill:
    price: float
    qty: float
    fee: float
    ts: int
    realized_pnl: Optional[float] = None


@dataclass
class ReduceEvent:
    symbol: str
    qty: float
    price: float
    fee: float
    realized_pnl: Optional[float]
    kind: str  # tp | sl | liquidation | adl | delist | manual | other
    ts: int
    tp_index: Optional[int] = None


@dataclass
class BrokerPosition:
    symbol: str
    side: Literal["LONG", "SHORT"]
    qty: float
    entry_price: float
    liq_price: float
    unrealized: float
    margin: float


ReduceHandler = Callable[[ReduceEvent], Awaitable[None]]
FundingHandler = Callable[[str, float, int], Awaitable[None]]
WarningHandler = Callable[[str], Awaitable[None]]


class Broker:
    kind: str = "base"

    def __init__(self) -> None:
        self.on_reduce: Optional[ReduceHandler] = None
        self.on_funding: Optional[FundingHandler] = None
        self.on_warning: Optional[WarningHandler] = None
        self.user_stream_state = "n/a"

    async def start(self) -> None:  # pragma: no cover - soyut
        raise NotImplementedError

    async def stop(self) -> None:  # pragma: no cover
        raise NotImplementedError

    async def account(self) -> AccountInfo:  # pragma: no cover
        raise NotImplementedError

    async def open(self, req: OpenRequest) -> Fill:  # pragma: no cover
        raise NotImplementedError

    async def close(self, symbol: str, side: str, qty: Optional[Decimal], tag: str) -> Optional[Fill]:  # pragma: no cover
        raise NotImplementedError

    async def replace_stop(self, symbol: str, side: str, stop: Decimal) -> None:  # pragma: no cover
        raise NotImplementedError

    async def replace_tps(self, symbol: str, side: str, tps: list[TPOrder]) -> None:  # pragma: no cover
        raise NotImplementedError

    async def cleanup(self, symbol: str) -> None:
        return None

    async def positions(self) -> dict[str, BrokerPosition]:  # pragma: no cover
        raise NotImplementedError

    async def fee_rates(self, symbol: str) -> tuple[float, float]:
        """(maker, taker)"""
        return 0.0002, 0.0005

    async def mmr_and_cap(self, symbol: str, notional: float, cls: str) -> tuple[float, int]:
        """(bakım marjin oranı, bu büyüklük için borsanın izin verdiği en yüksek kaldıraç)"""
        return default_mmr_cap(cls)

    async def on_price(self, symbol: str, last: Optional[float], mark: Optional[float], ts: int) -> None:
        return None


def default_mmr_cap(cls: str) -> tuple[float, int]:
    if cls == "major":
        return 0.004, 125
    if cls == "large":
        return 0.0065, 75
    return 0.01, 50
