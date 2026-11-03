"""Risk ve kasa yöneticisi: işlem riski çarpanları, portföy limitleri, devre kesiciler."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Callable, Optional

from pydantic import BaseModel

from ..db import Database
from ..exchange.broker_base import AccountInfo
from ..exchange.filters import D, check_qty, floor_step
from ..models import Position, SizingPlan, SymbolInfo, side_sign
from ..settings import RuntimeState, SettingsStore
from ..util import DAY_MS, now_ms, utc_day_start, utc_week_start
from .sizing import SizingInput, approx_liq_price, size_and_leverage


class Breaker(BaseModel):
    code: str
    label: str
    active: bool
    detail: str
    until: Optional[int] = None
    blocks_auto: bool = True


@dataclass
class RiskState:
    hwm: float = 0.0
    day_start_ts: int = 0
    day_start_equity: float = 0.0
    week_start_ts: int = 0
    week_start_equity: float = 0.0
    last_equity: float = 0.0


def open_risk_usdt(p: Position, mark: Optional[float]) -> float:
    """Stop tetiklenirse pozisyonun kaybettireceği tutar (kârı kilitlenmişse 0)."""
    px = mark or p.mark_price or p.entry_price
    return max(0.0, side_sign(p.side) * (px - p.stop_price) * p.qty)


class RiskManager:
    def __init__(self, store: SettingsStore, db: Database):
        self.store = store
        self.db = db
        self.states: dict[str, RiskState] = {}
        self._last_save = 0

    # --- özsermaye takibi ------------------------------------------------
    async def load(self, env: str) -> RiskState:
        raw = await self.db.kv_get(f"risk_state:{env}")
        st = RiskState(**raw) if raw else RiskState()
        self.states[env] = st
        return st

    def state(self, env: str) -> RiskState:
        return self.states.setdefault(env, RiskState())

    async def update_equity(self, env: str, equity: float, ts: Optional[int] = None) -> RiskState:
        ts = ts or now_ms()
        st = self.state(env)
        changed = False
        if st.hwm <= 0 or equity > st.hwm:
            st.hwm = equity
            changed = True
        day = utc_day_start(ts)
        if st.day_start_ts != day:
            st.day_start_ts = day
            st.day_start_equity = equity
            changed = True
        week = utc_week_start(ts)
        if st.week_start_ts != week:
            st.week_start_ts = week
            st.week_start_equity = equity
            changed = True
        st.last_equity = equity
        if changed or ts - self._last_save > 300_000:
            await self.db.kv_set(f"risk_state:{env}", asdict(st), ts)
            self._last_save = ts
        return st

    async def reset(self, env: str, equity: float) -> None:
        ts = now_ms()
        st = RiskState(
            hwm=equity, day_start_ts=utc_day_start(ts), day_start_equity=equity,
            week_start_ts=utc_week_start(ts), week_start_equity=equity, last_equity=equity,
        )
        self.states[env] = st
        await self.db.kv_set(f"risk_state:{env}", asdict(st), ts)

    def drawdown(self, env: str) -> float:
        st = self.state(env)
        if st.hwm <= 0:
            return 0.0
        return max(0.0, (st.hwm - st.last_equity) / st.hwm)

    def day_pnl(self, env: str) -> tuple[float, float]:
        st = self.state(env)
        if st.day_start_equity <= 0:
            return 0.0, 0.0
        pnl = st.last_equity - st.day_start_equity
        return pnl, pnl / st.day_start_equity * 100.0

    # --- devre kesiciler -------------------------------------------------
    def breakers(self, env: str, runtime: RuntimeState, recent_closed: list[Position], extra: Optional[list[Breaker]] = None) -> list[Breaker]:
        r = self.store.risk
        st = self.state(env)
        now = now_ms()
        out: list[Breaker] = [
            Breaker(
                code="paused", label="Manuel durdurma", active=runtime.paused,
                detail=runtime.paused_reason or "Yeni otomatik işlemler durduruldu",
            )
        ]
        daily = (st.day_start_equity - st.last_equity) / st.day_start_equity if st.day_start_equity > 0 else 0.0
        out.append(Breaker(
            code="daily_loss", label="Günlük zarar limiti", active=daily >= r.daily_loss_pct,
            detail=f"Bugün %{max(daily, 0) * 100:.2f} / limit %{r.daily_loss_pct * 100:.1f}",
            until=(st.day_start_ts + DAY_MS) if daily >= r.daily_loss_pct else None,
        ))
        weekly = (st.week_start_equity - st.last_equity) / st.week_start_equity if st.week_start_equity > 0 else 0.0
        out.append(Breaker(
            code="weekly_loss", label="Haftalık zarar limiti", active=weekly >= r.weekly_loss_pct,
            detail=f"Bu hafta %{max(weekly, 0) * 100:.2f} / limit %{r.weekly_loss_pct * 100:.1f}",
            until=(st.week_start_ts + 7 * DAY_MS) if weekly >= r.weekly_loss_pct else None,
        ))
        dd = self.drawdown(env)
        out.append(Breaker(
            code="max_drawdown", label="Maksimum düşüş", active=dd >= r.max_drawdown_pct,
            detail=f"Zirveden %{dd * 100:.1f} / limit %{r.max_drawdown_pct * 100:.0f}",
        ))
        streak, last_loss_at = loss_streak(recent_closed)
        until = (last_loss_at + r.loss_streak_cooldown_min * 60_000) if last_loss_at else None
        out.append(Breaker(
            code="loss_streak", label="Kayıp serisi soğuması",
            active=streak >= r.loss_streak_count and until is not None and now < until,
            detail=f"{streak} ardışık kayıp (eşik {r.loss_streak_count})", until=until,
        ))
        if extra:
            out.extend(extra)
        return out

    # --- çarpanlar -------------------------------------------------------
    def effective_max_positions(self, equity: float) -> int:
        r = self.store.risk
        if not r.capital_tiers:
            return r.max_positions
        tier = 1 if equity < 50 else 2 if equity < 200 else 3 if equity < 1000 else r.max_positions
        return max(1, min(r.max_positions, tier))

    def multipliers(self, env: str, score: float, regime_mult: float, streak: int, source: str) -> dict[str, float]:
        r = self.store.risk
        quality = 0.5 if score < 70 else 1.0 if score < 85 else 1.25
        dd = self.drawdown(env)
        if dd < 0.05:
            ddm = 1.0
        elif dd < 0.10:
            ddm = 0.75
        elif dd < 0.15:
            ddm = 0.5
        elif dd < r.max_drawdown_pct:
            ddm = 0.25
        else:
            ddm = 0.0 if source == "auto" else 0.25
        streak_m = 0.5 if streak >= r.loss_streak_count else 0.75 if streak >= 3 else 1.0
        out = {"kalite": quality, "düşüş": ddm, "seri": streak_m}
        if source in ("auto", "signal"):
            out["rejim"] = regime_mult
        return out

    # --- plan ------------------------------------------------------------
    def plan(
        self,
        *,
        env: str,
        symbol: str,
        side: str,
        entry: float,
        stop: float,
        score: float,
        source: str,
        info: SymbolInfo,
        account: AccountInfo,
        open_positions: list[Position],
        marks: Callable[[str], Optional[float]],
        regime_mult: float,
        taker_fee: float,
        slippage: float,
        mmr: float,
        bracket_cap: int,
        cls: str,
        corr: Callable[[str, str], float],
        streak: int,
        risk_pct_override: Optional[float] = None,
        leverage_override: Optional[int] = None,
    ) -> SizingPlan:
        r = self.store.risk
        equity = account.equity
        notes: list[str] = []
        sgn = side_sign(side)

        def reject(reason: str) -> SizingPlan:
            return SizingPlan(ok=False, reason=reason, notes=notes)

        if equity <= 0:
            return reject("Özsermaye sıfır veya negatif")
        if entry <= 0 or sgn * (entry - stop) <= 0:
            return reject("Stop fiyatı yanlış tarafta: long için girişin altında, short için üstünde olmalı")

        mult = self.multipliers(env, score, regime_mult, streak, source)
        if risk_pct_override is not None:
            risk_pct = min(max(risk_pct_override, r.min_risk_pct), r.max_risk_pct)
            notes.append(f"İşlem riski elle ayarlandı: %{risk_pct * 100:.2f}")
        else:
            risk_pct = r.base_risk_pct * math.prod(mult.values())
            if risk_pct <= 0:
                if mult.get("rejim", 1.0) <= 0:
                    return reject("Piyasa rejimi 'aşırı oynaklık': otomatik işlem açılmaz")
                return reject("Maksimum düşüş limitine ulaşıldı: otomatik işlemler durdu")
            risk_pct = min(max(risk_pct, r.min_risk_pct), r.max_risk_pct)

        cap = {"major": r.cap_major, "large": r.cap_large}.get(cls, r.cap_other)
        res = size_and_leverage(SizingInput(
            equity=equity, entry=entry, stop=stop, risk_pct=risk_pct, taker_fee=taker_fee, slippage=slippage,
            max_margin_pct=r.max_margin_per_trade_pct, mmr=mmr, class_cap=cap, bracket_cap=bracket_cap,
            global_cap=r.global_max_leverage, liq_buffer_k=r.liq_buffer_k, leverage_override=leverage_override,
        ))
        if res is None:
            return reject("Stop çok geniş: likidasyon güvenliğini sağlayan bir kaldıraç bulunamadı")
        if leverage_override is not None and leverage_override > res.lev_cap:
            notes.append(f"Kaldıraç güvenlik tavanına düşürüldü ({res.lev_cap}x)")
        if res.reduced:
            notes.append("İşlem başı marjin limiti nedeniyle pozisyon küçültüldü")
        notional = res.notional
        new_risk_per_notional = res.stop_distance + res.cost

        # --- portföy limitleri -------------------------------------------
        open_n = len(open_positions)
        eff_max = self.effective_max_positions(equity)
        if open_n >= eff_max:
            extra = " (sermayeye göre otomatik sınır)" if eff_max < r.max_positions else ""
            return reject(f"Eşzamanlı pozisyon limiti dolu ({open_n}/{eff_max}){extra}")
        same = sum(1 for p in open_positions if p.side == side)
        if same >= r.max_same_side:
            return reject(f"Aynı yönde pozisyon limiti dolu ({same}/{r.max_same_side})")
        open_risk = sum(open_risk_usdt(p, marks(p.symbol)) for p in open_positions)
        heat_room = r.max_portfolio_heat_pct * equity - open_risk
        if heat_room <= 0:
            return reject("Portföy risk limiti (açık risk) dolu")
        if notional * new_risk_per_notional > heat_room:
            notional = heat_room / new_risk_per_notional
            notes.append("Portföy risk limiti nedeniyle pozisyon küçültüldü")
        margin_used = sum(p.entry_price * p.qty / max(1, p.leverage) for p in open_positions)
        margin_room = r.max_total_margin_pct * equity - margin_used
        if margin_room <= 0:
            return reject("Toplam marjin kullanım limiti dolu")
        if notional / res.leverage > margin_room:
            notional = margin_room * res.leverage
            notes.append("Toplam marjin limiti nedeniyle pozisyon küçültüldü")
        total_notional = sum((marks(p.symbol) or p.entry_price) * p.qty for p in open_positions)
        eff_room = r.max_effective_leverage * equity - total_notional
        if eff_room <= 0:
            return reject("Portföy efektif kaldıraç limiti dolu")
        if notional > eff_room:
            notional = eff_room
            notes.append("Efektif kaldıraç limiti nedeniyle pozisyon küçültüldü")
        cluster = [p for p in open_positions if p.side == side and corr(symbol, p.symbol) >= r.cluster_corr]
        if cluster:
            cluster_risk = sum(open_risk_usdt(p, marks(p.symbol)) for p in cluster)
            room = r.max_cluster_risk_pct * equity - cluster_risk
            names = ", ".join(p.symbol for p in cluster[:3])
            if room <= 0:
                return reject(f"Korelasyon kümesi riski dolu ({names} ile yüksek korelasyon)")
            if notional * new_risk_per_notional > room:
                notional = room / new_risk_per_notional
                notes.append(f"Yüksek korelasyon ({names}) nedeniyle pozisyon küçültüldü")

        # --- kaldıraç: küçülen büyüklük için yeniden en düşük uygun değer ----
        leverage = res.leverage
        if leverage_override is None:
            max_margin = equity * r.max_margin_per_trade_pct
            leverage = max(1, min(res.lev_cap, math.ceil(notional / max_margin)))

        # --- borsa filtreleri --------------------------------------------
        qty = floor_step(D(notional) / D(entry), info.step_size)
        err = check_qty(info, qty, entry)
        if err:
            return reject(f"{err}. Sermaye bu işlem için küçük olabilir.")
        notional = float(qty) * entry
        margin = notional / leverage
        risk_usdt = notional * new_risk_per_notional
        liq = approx_liq_price(side, entry, leverage, mmr)
        liq_dist = abs(entry - liq) / entry
        notes.insert(0, f"Kaldıraç {leverage}x: marjin bütçesine sığan en düşük değer (güvenlik tavanı {res.lev_cap}x)")
        if res.stop_distance > 0:
            notes.insert(1, f"Likidasyon fiyatı stop'tan yaklaşık {liq_dist / res.stop_distance:.1f} kat uzakta")
        return SizingPlan(
            ok=True,
            notional=round(notional, 4),
            qty=float(qty),
            leverage=leverage,
            margin=round(margin, 4),
            risk_usdt=round(risk_usdt, 4),
            risk_pct=round(risk_usdt / equity, 6),
            stop_distance_pct=round(res.stop_distance * 100.0, 4),
            liq_price=liq,
            liq_distance_pct=round(liq_dist * 100.0, 3),
            fee_estimate=round(notional * taker_fee * 2, 4),
            mmr=mmr,
            notes=notes,
            multipliers={k: round(v, 3) for k, v in mult.items()},
        )


def loss_streak(recent_closed: list[Position]) -> tuple[int, Optional[int]]:
    """En yeni kapanıştan geriye ardışık zarar sayısı ve son zararın zamanı."""
    items = sorted((p for p in recent_closed if p.closed_at), key=lambda p: p.closed_at or 0, reverse=True)
    n = 0
    last_loss_at: Optional[int] = None
    for p in items:
        net = p.realized_pnl - p.fees + p.funding
        if net < 0:
            n += 1
            if last_loss_at is None:
                last_loss_at = p.closed_at
        else:
            break
    return n, last_loss_at
