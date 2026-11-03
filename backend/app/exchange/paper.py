"""Simülasyon (paper) broker: gerçek piyasa fiyatlarıyla sanal emirler.

- Giriş/çıkış: anlık fiyat ± kayma, taker komisyonu
- Stop-loss: mark fiyatla tetiklenir (borsadaki MARK_PRICE ayarı gibi)
- Take-profit: son fiyat hedefi geçince limit fiyattan dolar (maker komisyonu)
- Likidasyon: mark fiyat yaklaşık likidasyon fiyatını geçerse
- Funding: funding zamanı geçtiğinde açık pozisyona uygulanır
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Callable, Optional

from ..db import Database
from ..market.base import MarketData
from ..risk.sizing import approx_liq_price
from ..settings import PaperSettings
from ..util import now_ms
from .broker_base import (
    AccountInfo,
    Broker,
    BrokerError,
    BrokerPosition,
    Fill,
    OpenRequest,
    ReduceEvent,
    TPOrder,
)

log = logging.getLogger(__name__)


@dataclass
class PaperPos:
    symbol: str
    side: str
    qty: float
    entry: float
    leverage: int
    stop: Optional[float]
    liq: float
    opened_at: int
    tps: list[dict] = field(default_factory=list)  # {"index", "price", "qty"}

    @property
    def sign(self) -> int:
        return 1 if self.side == "LONG" else -1


class PaperBroker(Broker):
    kind = "paper"

    def __init__(self, db: Database, market: MarketData, settings: Callable[[], PaperSettings]):
        super().__init__()
        self.db = db
        self.market = market
        self.settings = settings
        self.wallet = 0.0
        self.pos: dict[str, PaperPos] = {}
        self._funding_ref: dict[str, tuple[int, float]] = {}

    # --- durum ---------------------------------------------------------
    async def start(self) -> None:
        state = await self.db.kv_get("paper_state")
        if state:
            self.wallet = float(state.get("wallet", 0.0))
            self.pos = {k: PaperPos(**v) for k, v in state.get("positions", {}).items()}
        else:
            self.wallet = float(self.settings().starting_balance)
            await self._save()

    async def stop(self) -> None:
        await self._save()

    async def _save(self) -> None:
        await self.db.kv_set(
            "paper_state",
            {"wallet": self.wallet, "positions": {k: asdict(v) for k, v in self.pos.items()}},
            now_ms(),
        )

    async def reset(self, balance: float) -> None:
        self.wallet = float(balance)
        self.pos.clear()
        await self._save()

    def _price(self, symbol: str) -> float:
        p = self.market.price(symbol)
        if not p or p <= 0:
            raise BrokerError(f"{symbol} için fiyat verisi yok")
        return p

    # --- sorgular ------------------------------------------------------
    async def account(self) -> AccountInfo:
        unreal = 0.0
        margin = 0.0
        for p in self.pos.values():
            mk = self.market.mark(p.symbol) or p.entry
            unreal += p.sign * (mk - p.entry) * p.qty
            margin += p.entry * p.qty / max(1, p.leverage)
        return AccountInfo(
            equity=self.wallet + unreal,
            wallet=self.wallet,
            unrealized=unreal,
            available=self.wallet - margin,
            margin_used=margin,
        )

    async def positions(self) -> dict[str, BrokerPosition]:
        out: dict[str, BrokerPosition] = {}
        for s, p in self.pos.items():
            mk = self.market.mark(s) or p.entry
            out[s] = BrokerPosition(
                symbol=s, side=p.side, qty=p.qty, entry_price=p.entry, liq_price=p.liq,  # type: ignore[arg-type]
                unrealized=p.sign * (mk - p.entry) * p.qty, margin=p.entry * p.qty / max(1, p.leverage),
            )
        return out

    async def fee_rates(self, symbol: str) -> tuple[float, float]:
        cfg = self.settings()
        return cfg.maker_fee, cfg.taker_fee

    # --- emirler -------------------------------------------------------
    async def open(self, req: OpenRequest) -> Fill:
        if req.symbol in self.pos:
            raise BrokerError("Bu sembolde zaten açık pozisyon var")
        cfg = self.settings()
        px = self._price(req.symbol)
        sgn = 1 if req.side == "LONG" else -1
        fill = px * (1 + sgn * cfg.slippage_bps / 10_000)
        if req.entry_mode == "ioc" and abs(fill / req.ref_price - 1.0) > req.max_slippage_pct:
            raise BrokerError("Fiyat kayma sınırını aştı; emir dolmadı (IOC)")
        qty = float(req.qty)
        notional = fill * qty
        fee = notional * cfg.taker_fee
        margin = notional / max(1, req.leverage)
        acct = await self.account()
        if margin + fee > acct.available:
            raise BrokerError(
                f"Marjin yetersiz: gereken {margin + fee:.2f} USDT, kullanılabilir {acct.available:.2f} USDT"
            )
        self.wallet -= fee
        self.pos[req.symbol] = PaperPos(
            symbol=req.symbol,
            side=req.side,
            qty=qty,
            entry=fill,
            leverage=req.leverage,
            stop=float(req.stop_price),
            liq=approx_liq_price(req.side, fill, req.leverage, req.mmr),
            opened_at=now_ms(),
            tps=[{"index": t.index, "price": float(t.price), "qty": float(t.qty)} for t in req.tps],
        )
        await self._save()
        return Fill(price=fill, qty=qty, fee=fee, ts=now_ms())

    def _reduce(self, p: PaperPos, q: float, price: float, fee_rate: float) -> tuple[float, float]:
        pnl = p.sign * (price - p.entry) * q
        fee = price * q * fee_rate
        self.wallet += pnl - fee
        p.qty -= q
        if p.qty <= 1e-12:
            self.pos.pop(p.symbol, None)
        return pnl, fee

    async def close(self, symbol: str, side: str, qty: Optional[Decimal], tag: str) -> Optional[Fill]:
        p = self.pos.get(symbol)
        if p is None:
            return None
        cfg = self.settings()
        q = p.qty if qty is None else min(float(qty), p.qty)
        px = self._price(symbol)
        fill = px * (1 - p.sign * cfg.slippage_bps / 10_000)
        pnl, fee = self._reduce(p, q, fill, cfg.taker_fee)
        await self._save()
        return Fill(price=fill, qty=q, fee=fee, ts=now_ms(), realized_pnl=pnl)

    async def replace_stop(self, symbol: str, side: str, stop: Decimal) -> None:
        p = self.pos.get(symbol)
        if p is None:
            raise BrokerError("Pozisyon bulunamadı")
        p.stop = float(stop)
        await self._save()

    async def replace_tps(self, symbol: str, side: str, tps: list[TPOrder]) -> None:
        p = self.pos.get(symbol)
        if p is None:
            raise BrokerError("Pozisyon bulunamadı")
        p.tps = [{"index": t.index, "price": float(t.price), "qty": float(t.qty)} for t in tps]
        await self._save()

    # --- fiyat güncellemesi: tetiklemeler --------------------------------
    async def on_price(self, symbol: str, last: Optional[float], mark: Optional[float], ts: int) -> None:
        p = self.pos.get(symbol)
        if p is None or not last or not mark:
            return
        cfg = self.settings()
        sgn = p.sign
        changed = False
        # likidasyon
        if (sgn > 0 and mark <= p.liq) or (sgn < 0 and mark >= p.liq):
            q = p.qty
            pnl, fee = self._reduce(p, q, p.liq, cfg.taker_fee)
            await self._save()
            await self._emit(ReduceEvent(symbol, q, p.liq, fee, pnl, "liquidation", ts))
            return
        # stop-loss (mark fiyatla tetiklenir, piyasa emriyle dolar)
        if p.stop and ((sgn > 0 and mark <= p.stop) or (sgn < 0 and mark >= p.stop)):
            base = min(p.stop, last) if sgn > 0 else max(p.stop, last)
            fill = base * (1 - sgn * cfg.slippage_bps / 10_000)
            q = p.qty
            pnl, fee = self._reduce(p, q, fill, cfg.taker_fee)
            await self._save()
            await self._emit(ReduceEvent(symbol, q, fill, fee, pnl, "sl", ts))
            return
        # take-profit limitleri (son fiyatla)
        for tp in sorted(list(p.tps), key=lambda x: x["index"]):
            hit = (sgn > 0 and last >= tp["price"]) or (sgn < 0 and last <= tp["price"])
            if not hit:
                continue
            p.tps.remove(tp)
            q = min(tp["qty"], p.qty)
            if q <= 0:
                continue
            pnl, fee = self._reduce(p, q, tp["price"], cfg.maker_fee)
            changed = True
            await self._emit(ReduceEvent(symbol, q, tp["price"], fee, pnl, "tp", ts, tp_index=tp["index"]))
            if symbol not in self.pos:
                break
        # funding
        mi = self.market.funding(symbol)
        if mi and mi.next_funding_time and symbol in self.pos:
            ref = self._funding_ref.get(symbol)
            if ref and mi.next_funding_time > ref[0] and p.opened_at < ref[0]:
                amount = -sgn * ref[1] * mark * p.qty
                self.wallet += amount
                changed = True
                if self.on_funding:
                    await self.on_funding(symbol, amount, ts)
            self._funding_ref[symbol] = (mi.next_funding_time, mi.funding_rate)
        if changed:
            await self._save()

    async def _emit(self, ev: ReduceEvent) -> None:
        if self.on_reduce:
            await self.on_reduce(ev)
