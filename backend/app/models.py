"""Alan modelleri: mum, sembol, sinyal, pozisyon, büyüklük planı."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, Field

Side = Literal["LONG", "SHORT"]
Env = Literal["paper", "testnet", "live"]
SignalStatus = Literal["ACTIVE", "EXECUTED", "DISMISSED", "EXPIRED", "REJECTED", "FAILED"]


def side_sign(side: str) -> int:
    return 1 if side == "LONG" else -1


@dataclass(slots=True)
class Candle:
    t: int  # açılış zamanı (ms)
    o: float
    h: float
    l: float  # noqa: E741
    c: float
    v: float = 0.0  # baz varlık hacmi
    qv: float = 0.0  # USDT hacmi
    tbq: float = 0.0  # agresif (taker) alış USDT hacmi
    closed: bool = True

    def to_chart(self) -> dict:
        return {
            "time": self.t // 1000,
            "open": self.o,
            "high": self.h,
            "low": self.l,
            "close": self.c,
            "volume": self.qv if self.qv > 0 else self.v,
        }


class SymbolInfo(BaseModel):
    symbol: str
    base: str = ""
    quote: str = "USDT"
    tick_size: str = "0.01"
    step_size: str = "0.001"
    market_step_size: str = "0.001"
    min_qty: str = "0.001"
    max_qty: str = "1000000"
    market_max_qty: str = "1000000"
    min_notional: str = "5"
    onboard_date: int = 0
    delivery_date: int = 0
    status: str = "TRADING"
    contract_type: str = "PERPETUAL"


class TakeProfit(BaseModel):
    r: float
    price: float
    fraction: float
    filled: bool = False
    filled_at: Optional[int] = None


class SizingPlan(BaseModel):
    ok: bool = False
    reason: str = ""
    notional: float = 0.0
    qty: float = 0.0
    leverage: int = 1
    margin: float = 0.0
    risk_usdt: float = 0.0
    risk_pct: float = 0.0
    stop_distance_pct: float = 0.0
    liq_price: float = 0.0
    liq_distance_pct: float = 0.0
    fee_estimate: float = 0.0
    mmr: float = 0.0
    notes: list[str] = Field(default_factory=list)
    multipliers: dict[str, float] = Field(default_factory=dict)


class Signal(BaseModel):
    id: str
    symbol: str
    side: Side
    strategy: str
    strategy_name: str
    timeframe: str
    created_at: int
    expires_at: int
    bar_time: int
    entry: float
    stop: float
    take_profits: list[TakeProfit] = Field(default_factory=list)
    trailing_atr_mult: Optional[float] = None
    time_stop_bars: int = 0
    atr: float = 0.0
    score: float = 0.0
    ev_r: float = 0.0
    win_prob: float = 0.0
    rr: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    features: dict[str, float] = Field(default_factory=dict)
    status: SignalStatus = "ACTIVE"
    status_note: str = ""
    plan: Optional[SizingPlan] = None
    position_id: Optional[str] = None
    price: Optional[float] = None


class PositionEvent(BaseModel):
    ts: int
    kind: str
    message: str
    price: Optional[float] = None
    qty: Optional[float] = None


class Position(BaseModel):
    id: str
    env: Env
    symbol: str
    side: Side
    strategy: str
    strategy_name: str
    signal_id: Optional[str] = None
    source: Literal["auto", "manual"] = "manual"
    status: Literal["OPEN", "CLOSED"] = "OPEN"
    opened_at: int
    closed_at: Optional[int] = None
    timeframe: str = "15m"
    entry_price: float
    qty: float
    initial_qty: float
    leverage: int
    margin: float
    notional: float
    stop_price: float
    initial_stop: float
    take_profits: list[TakeProfit] = Field(default_factory=list)
    trailing_atr_mult: Optional[float] = None
    trailing_active: bool = False
    atr: float = 0.0
    liq_price: float = 0.0
    time_stop_at: Optional[int] = None
    realized_pnl: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    exit_qty: float = 0.0
    exit_value: float = 0.0
    exit_price: Optional[float] = None
    close_reason: Optional[str] = None
    risk_usdt: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    r_final: Optional[float] = None
    best_price: float = 0.0
    worst_price: float = 0.0
    breakeven_done: bool = False
    events: list[PositionEvent] = Field(default_factory=list)
    # Anlık (runtime) alanlar
    mark_price: Optional[float] = None
    unrealized_pnl: float = 0.0
    pnl_total: float = 0.0
    roe_pct: float = 0.0
    r_now: float = 0.0
    liq_distance_pct: Optional[float] = None
    stop_distance_pct: Optional[float] = None

    @property
    def sign(self) -> int:
        return side_sign(self.side)

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry_price - self.initial_stop)

    def add_event(self, ts: int, kind: str, message: str, price: float | None = None, qty: float | None = None) -> None:
        self.events.append(PositionEvent(ts=ts, kind=kind, message=message, price=price, qty=qty))
        if len(self.events) > 200:
            self.events = self.events[-200:]

    def refresh_runtime(self, mark: float | None) -> None:
        """Anlık K/Z, R, mesafe alanlarını günceller."""
        if mark is None or mark <= 0:
            return
        self.mark_price = mark
        s = self.sign
        if self.status == "OPEN":
            self.unrealized_pnl = s * (mark - self.entry_price) * self.qty
        else:
            self.unrealized_pnl = 0.0
        self.pnl_total = self.realized_pnl + self.unrealized_pnl - self.fees + self.funding
        self.roe_pct = (self.pnl_total / self.margin * 100.0) if self.margin > 0 else 0.0
        rpu = self.risk_per_unit
        self.r_now = (s * (mark - self.entry_price) / rpu) if rpu > 0 else 0.0
        if self.status == "OPEN":
            self.mfe_r = max(self.mfe_r, self.r_now)
            self.mae_r = min(self.mae_r, self.r_now)
            if self.best_price <= 0:
                self.best_price = mark
                self.worst_price = mark
            if s > 0:
                self.best_price = max(self.best_price, mark)
                self.worst_price = min(self.worst_price, mark)
            else:
                self.best_price = min(self.best_price, mark)
                self.worst_price = max(self.worst_price, mark)
            self.liq_distance_pct = (abs(mark - self.liq_price) / mark * 100.0) if self.liq_price > 0 else None
            self.stop_distance_pct = abs(mark - self.stop_price) / mark * 100.0 if self.stop_price > 0 else None
