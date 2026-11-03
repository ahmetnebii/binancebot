"""Backtest simülatörü: canlı sistemle AYNI strateji, rejim, skor ve risk kodunu geçmiş veride çalıştırır.

Gerçekçilik kuralları:
- Sinyal bar kapanışında üretilir, işleme bir SONRAKİ barın açılışında girilir (gelecek bilgisi yok).
- Giriş/çıkışta kayma ve taker komisyonu, kâr hedeflerinde maker komisyonu uygulanır.
- Aynı mumda hem stop hem hedef görülürse önce stop varsayılır (kötümser).
- Funding, gerçek geçmiş oranlarıyla funding anında açık pozisyonlara uygulanır.
- Pozisyon yönetimi canlı motorla aynıdır: TP merdiveni, ilk TP sonrası başa-baş, iz süren stop,
  zaman stopu; devre kesiciler (günlük/haftalık zarar, maksimum düşüş, kayıp serisi) simüle zamanla çalışır.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Callable, Optional

import numpy as np

from ..exchange.broker_base import AccountInfo, default_mmr_cap
from ..features.indicators import aggregate_bars, atr, correlation, log_returns
from ..features.regime import RegimeDetector
from ..market.base import MarkInfo
from ..models import Position, SymbolInfo, side_sign
from ..risk.manager import RiskManager, RiskState
from ..risk.sizing import symbol_class
from ..settings import RiskSettings, ScannerSettings, StrategySettings
from ..strategies.base import StrategyContext, side_allowed
from ..strategies.mean_reversion import MeanReversion
from ..strategies.rs_rotation import RSRotation
from ..strategies.scorer import estimate
from ..strategies.trend_breakout import TrendBreakout
from ..util import DAY_MS, utc_week_start
from .data import BacktestCancelled, HistoryData

STEP = 900_000
HOUR = 3_600_000
FUNDING_DEFAULT = 0.0001
MAX_BARS_15M = 500
MAX_BARS_1H = 400


@dataclass
class BacktestConfig:
    symbols: list[str]
    start_ms: int
    end_ms: int
    initial_equity: float
    s1: bool
    s2: bool
    s3: bool
    slippage_bps: float
    taker_fee: float
    maker_fee: float
    risk: RiskSettings
    strategies: StrategySettings
    scanner: ScannerSettings


class SliceBuffer:
    """Önceden yüklenmiş dizilerin 'şu ana kadarki' kısmına sıfır kopyalı görünüm."""

    __slots__ = ("_a", "_start", "_end", "_cache")

    def __init__(self, arrays: dict[str, np.ndarray]):
        self._a = arrays
        self._start = 0
        self._end = 0
        self._cache: Optional[dict[str, np.ndarray]] = None

    def set_end(self, end: int, maxlen: int) -> None:
        if end != self._end:
            self._end = end
            self._start = max(0, end - maxlen)
            self._cache = None

    def __len__(self) -> int:
        return self._end - self._start

    def arrays(self) -> dict[str, np.ndarray]:
        if self._cache is None:
            s, e = self._start, self._end
            self._cache = {k: v[s:e] for k, v in self._a.items()}
        return self._cache


class HistView:
    """Stratejiler ve rejim dedektörü için piyasa benzeri görünüm."""

    def __init__(self, universe: list[str]):
        self.universe = universe
        self.bufs: dict[tuple[str, str], SliceBuffer] = {}
        self.marks: dict[str, MarkInfo] = {}  # yerinde güncellenir (her barda yeni nesne üretilmez)

    def buffer(self, symbol: str, tf: str) -> Optional[SliceBuffer]:
        return self.bufs.get((symbol, tf))

    def funding(self, symbol: str) -> Optional[MarkInfo]:
        return self.marks.get(symbol)


@dataclass
class SimPos:
    id: int
    symbol: str
    side: str
    strategy: str
    strategy_name: str
    timeframe: str
    entry_time: int
    entry_bar: int
    entry: float
    qty: float
    initial_qty: float
    stop: float
    initial_stop: float
    leverage: int
    risk_usdt: float
    risk_frac: float
    tps: list[dict] = field(default_factory=list)  # {"price", "qty", "filled"}
    trailing_mult: Optional[float] = None
    trailing_active: bool = False
    be_done: bool = False
    time_stop_at: Optional[int] = None
    realized: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    exit_qty: float = 0.0
    exit_value: float = 0.0
    best: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0

    @property
    def sgn(self) -> int:
        return side_sign(self.side)


class Backtester:
    def __init__(
        self,
        cfg: BacktestConfig,
        data: HistoryData,
        infos: dict[str, SymbolInfo],
        progress: Callable[[float, str], None],
        cancelled: Callable[[], bool],
    ):
        self.cfg = cfg
        self.data = data
        self.infos = infos
        self.progress = progress
        self.cancelled = cancelled
        self.symbols = [s for s in cfg.symbols if s in data.bars]
        self.slip = cfg.slippage_bps / 10_000.0
        self.view = HistView(self.symbols)
        self.rm = RiskManager(SimpleNamespace(risk=cfg.risk), None)  # type: ignore[arg-type]
        self.regime = RegimeDetector()
        self.s1, self.s2, self.s3 = TrendBreakout(), RSRotation(), MeanReversion()
        self.positions: dict[str, SimPos] = {}
        self.pending: dict[str, dict] = {}
        self.trades: list[dict] = []
        self.cash = cfg.initial_equity
        self.eq_t: list[int] = []
        self.eq_v: list[float] = []
        self.warnings: list[str] = []
        self.skipped = {"drift": 0, "limits": 0, "ev": 0, "gap": 0}
        self._pid = 0
        self._corr_cache: dict[tuple[str, str, int], float] = {}
        self._exposed_bars = 0
        self._total_bars = 0
        self.fees_total = 0.0
        self.funding_total = 0.0
        # devre kesici durumu
        self.hwm = cfg.initial_equity
        self.day_start = (0, cfg.initial_equity)
        self.week_start = (0, cfg.initial_equity)
        self.halted_until = 0
        self.dd_halt = False
        self.loss_streak = 0

    # --- hazırlık --------------------------------------------------------
    def _prepare(self) -> None:
        self.h1: dict[str, dict[str, np.ndarray]] = {}
        self.h1_count: dict[str, np.ndarray] = {}
        self.ptr: dict[str, int] = {}
        vols: list[tuple[float, str]] = []
        for s in self.symbols:
            a = self.data.bars[s]
            h = aggregate_bars(a, HOUR, closed_only=False)
            self.h1[s] = h
            self.h1_count[s] = np.searchsorted(h["t"] + HOUR, a["t"] + STEP, side="right")
            b15 = SliceBuffer(a)
            b1h = SliceBuffer(h)
            self.view.bufs[(s, "15m")] = b15
            self.view.bufs[(s, "1h")] = b1h
            self.ptr[s] = 0
            vols.append((float(a["qv"].sum()), s))
        vols.sort(reverse=True)
        self.rank = {s: i + 1 for i, (_, s) in enumerate(vols)}
        self.fptr = {s: 0 for s in self.symbols}
        all_t = np.unique(np.concatenate([self.data.bars[s]["t"] for s in self.symbols])) if self.symbols else np.array([], dtype=np.int64)
        self.timeline = all_t[all_t < self.cfg.end_ms]

    # --- yardımcılar -----------------------------------------------------
    def _bar(self, s: str) -> Optional[int]:
        """Mevcut zamandaki barın dizideki indeksi (o sembolde bar yoksa None)."""
        p = self.ptr[s]
        if p == 0:
            return None
        a = self.data.bars[s]
        return p - 1 if int(a["t"][p - 1]) == self._T else None

    def _close(self, s: str) -> Optional[float]:
        p = self.ptr[s]
        return float(self.data.bars[s]["c"][p - 1]) if p > 0 else None

    def _unrealized(self) -> float:
        u = 0.0
        for p in self.positions.values():
            c = self._close(p.symbol)
            if c is not None:
                u += p.sgn * (c - p.entry) * p.qty
        return u

    def _equity(self) -> float:
        return self.cash + self._unrealized()

    def _margin_used(self) -> float:
        return sum(p.entry * p.qty / max(1, p.leverage) for p in self.positions.values())

    def _corr(self, a: str, b: str) -> float:
        if a == b:
            return 1.0
        key = (min(a, b), max(a, b), self._T // (24 * HOUR))
        if key in self._corr_cache:
            return self._corr_cache[key]
        ba, bb = self.view.buffer(a, "1h"), self.view.buffer(b, "1h")
        v = 0.0
        if ba is not None and bb is not None and len(ba) > 80 and len(bb) > 80:
            v = correlation(log_returns(ba.arrays()["c"][-73:]), log_returns(bb.arrays()["c"][-73:]))
        self._corr_cache[key] = v
        return v

    def _as_position(self, p: SimPos) -> Position:
        c = self._close(p.symbol) or p.entry
        return Position(
            id=f"bt{p.id}", env="paper", symbol=p.symbol, side=p.side, strategy=p.strategy,  # type: ignore[arg-type]
            strategy_name=p.strategy_name, opened_at=p.entry_time, entry_price=p.entry, qty=p.qty,
            initial_qty=p.initial_qty, leverage=p.leverage, margin=p.entry * p.initial_qty / max(1, p.leverage),
            notional=p.entry * p.initial_qty, stop_price=p.stop, initial_stop=p.initial_stop,
            risk_usdt=p.risk_usdt, mark_price=c,
        )

    def _funding_rate_at(self, s: str, t: int) -> float:
        f = self.data.funding.get(s)
        if not f or len(f[0]) == 0:
            return FUNDING_DEFAULT
        i = int(np.searchsorted(f[0], t, side="right")) - 1
        return float(f[1][i]) if i >= 0 else float(f[1][0])

    # --- pozisyon işlemleri ------------------------------------------------
    def _reduce(self, p: SimPos, qty: float, price: float, fee_rate: float) -> None:
        q = min(qty, p.qty)
        if q <= 0:
            return
        pnl = p.sgn * (price - p.entry) * q
        fee = price * q * fee_rate
        p.qty -= q
        p.realized += pnl
        p.fees += fee
        p.exit_qty += q
        p.exit_value += q * price
        self.cash += pnl - fee
        self.fees_total += fee

    def _finalize(self, p: SimPos, reason: str, t: int, bar_idx: int) -> None:
        net = p.realized - p.fees + p.funding
        r = net / p.risk_usdt if p.risk_usdt > 0 else 0.0
        self.trades.append({
            "symbol": p.symbol, "side": p.side, "strategy": p.strategy, "strategy_name": p.strategy_name,
            "entry_time": p.entry_time, "exit_time": t, "entry_price": p.entry,
            "exit_price": (p.exit_value / p.exit_qty) if p.exit_qty > 0 else p.entry,
            "r": r, "pnl": net, "fees": p.fees, "funding": p.funding, "reason": reason,
            "hold_bars": max(1, bar_idx - p.entry_bar + 1), "risk_frac": p.risk_frac,
            "mfe_r": p.mfe_r, "mae_r": p.mae_r,
        })
        self.positions.pop(p.symbol, None)
        if net < 0:
            self.loss_streak += 1
            if self.loss_streak >= self.cfg.risk.loss_streak_count:
                self.halted_until = max(self.halted_until, t + self.cfg.risk.loss_streak_cooldown_min * 60_000)
        else:
            self.loss_streak = 0

    def _open_pending(self, s: str, i: int) -> None:
        pend = self.pending.pop(s, None)
        if pend is None:
            return
        a = self.data.bars[s]
        sig, plan = pend["sig"], pend["plan"]
        sgn = side_sign(sig.side)
        o = float(a["o"][i])
        if sgn * (o - sig.stop) <= 0:
            self.skipped["gap"] += 1
            return
        risk_unit_sig = abs(sig.entry - sig.stop)
        if risk_unit_sig > 0 and sgn * (o - sig.entry) / risk_unit_sig > self.cfg.risk.max_entry_drift_r:
            self.skipped["drift"] += 1
            return
        fill = o * (1 + sgn * self.slip)
        qty = float(plan.qty)
        if qty <= 0:
            return
        fee = fill * qty * self.cfg.taker_fee
        self.cash -= fee
        self.fees_total += fee
        risk_unit = abs(o - sig.stop)
        tps = [{"price": o + sgn * tp.r * risk_unit, "qty": qty * tp.fraction, "filled": False} for tp in sig.take_profits]
        self._pid += 1
        eq = self._equity()
        tf_ms = HOUR if sig.timeframe == "1h" else STEP
        p = SimPos(
            id=self._pid, symbol=s, side=sig.side, strategy=sig.strategy, strategy_name=sig.strategy_name,
            timeframe=sig.timeframe, entry_time=self._T, entry_bar=i, entry=fill, qty=qty, initial_qty=qty,
            stop=sig.stop, initial_stop=sig.stop, leverage=plan.leverage,
            risk_usdt=abs(fill - sig.stop) * qty, risk_frac=(abs(fill - sig.stop) * qty / eq) if eq > 0 else 0.0,
            tps=tps, trailing_mult=sig.trailing_atr_mult, fees=fee, best=fill,
            time_stop_at=(self._T + sig.time_stop_bars * tf_ms) if sig.time_stop_bars else None,
        )
        self.positions[s] = p

    def _process_bar(self, p: SimPos, i: int) -> None:
        a = self.data.bars[p.symbol]
        o, h, l = float(a["o"][i]), float(a["h"][i]), float(a["l"][i])  # noqa: E741
        sgn = p.sgn
        rpu = abs(p.entry - p.initial_stop) or 1e-12
        # stop (önce): boşlukla açılış stop'u geçtiyse açılıştan dolar
        if (sgn > 0 and l <= p.stop) or (sgn < 0 and h >= p.stop):
            base = min(p.stop, o) if sgn > 0 else max(p.stop, o)
            fill = base * (1 - sgn * self.slip)
            reason = "İz süren stop" if p.trailing_active and p.be_done else ("Başa-baş stop" if p.be_done else "Stop-loss")
            self._reduce(p, p.qty, fill, self.cfg.taker_fee)
            p.mae_r = min(p.mae_r, sgn * (base - p.entry) / rpu)
            self._finalize(p, reason, self._T + STEP, i)
            return
        # hedefler (limit, maker)
        for tp in p.tps:
            if tp["filled"] or p.qty <= 0:
                continue
            if (sgn > 0 and h >= tp["price"]) or (sgn < 0 and l <= tp["price"]):
                self._reduce(p, tp["qty"], tp["price"], self.cfg.maker_fee)
                tp["filled"] = True
                if not p.be_done:
                    be = p.entry * (1 + sgn * 2 * self.cfg.taker_fee)
                    if sgn * (be - p.stop) > 0:
                        p.stop = be
                    p.be_done = True
                if p.trailing_mult:
                    p.trailing_active = True
        if p.qty <= max(1e-12, p.initial_qty * 1e-6):
            self._finalize(p, "Kâr hedefleri tamamlandı", self._T + STEP, i)
            return
        ext = h if sgn > 0 else l
        p.best = max(p.best, ext) if sgn > 0 else min(p.best, ext)
        p.mfe_r = max(p.mfe_r, sgn * (ext - p.entry) / rpu)
        p.mae_r = min(p.mae_r, sgn * ((l if sgn > 0 else h) - p.entry) / rpu)

    def _manage_on_close(self, p: SimPos, i: int, hour_close: bool) -> None:
        if p.timeframe == "1h" and not hour_close:
            return
        buf = self.view.buffer(p.symbol, p.timeframe)
        if buf is None or len(buf) < 20:
            return
        arr = buf.arrays()
        c = float(arr["c"][-1])
        at = float(atr(arr["h"][-100:], arr["l"][-100:], arr["c"][-100:], 14)[-1])
        if p.trailing_active and p.trailing_mult and math.isfinite(at) and at > 0:
            cand = p.best - p.sgn * p.trailing_mult * at
            if p.sgn > 0 and cand > p.stop + 0.25 * at and cand < c - 0.1 * at:
                p.stop = cand
            elif p.sgn < 0 and cand < p.stop - 0.25 * at and cand > c + 0.1 * at:
                p.stop = cand
        if p.time_stop_at and self._T + STEP >= p.time_stop_at and p.mfe_r < 0.5 and not any(t["filled"] for t in p.tps):
            close = self._close(p.symbol) or c
            self._reduce(p, p.qty, close * (1 - p.sgn * self.slip), self.cfg.taker_fee)
            self._finalize(p, "Zaman stopu", self._T + STEP, i)

    # --- sinyaller -------------------------------------------------------
    def _entries_blocked(self, t: int) -> bool:
        return self.dd_halt or t < self.halted_until

    def _consider(self, sig, t: int) -> None:
        s = sig.symbol
        if s in self.positions or s in self.pending:
            return
        if not side_allowed(sig.strategy, sig.side, self.cfg.strategies):
            return
        mi = self.view.marks.get(s)
        _, ev = estimate(sig, self.cfg.taker_fee, self.slip, mi.funding_rate if mi else 0.0)
        sig.ev_r = ev
        if ev < self.cfg.risk.min_ev_r:
            self.skipped["ev"] += 1
            return
        eq = self._equity()
        acct = AccountInfo(equity=eq, wallet=self.cash, unrealized=eq - self.cash,
                           available=self.cash - self._margin_used(), margin_used=self._margin_used())
        cls = symbol_class(s, self.rank.get(s, 999))
        mmr, cap = default_mmr_cap(cls)
        info = self.infos.get(s) or SymbolInfo(symbol=s, step_size="0.000001", market_step_size="0.000001", min_qty="0.000001", min_notional="5")
        open_like = [self._as_position(p) for p in self.positions.values()]
        for ps, pend in self.pending.items():  # bekleyen girişler de limitlere sayılır
            ps_sig, ps_plan = pend["sig"], pend["plan"]
            open_like.append(Position(
                id=f"pend-{ps}", env="paper", symbol=ps, side=ps_sig.side, strategy=ps_sig.strategy,
                strategy_name=ps_sig.strategy_name, opened_at=t, entry_price=ps_sig.entry, qty=ps_plan.qty,
                initial_qty=ps_plan.qty, leverage=ps_plan.leverage, margin=ps_plan.margin, notional=ps_plan.notional,
                stop_price=ps_sig.stop, initial_stop=ps_sig.stop, risk_usdt=ps_plan.risk_usdt, mark_price=ps_sig.entry,
            ))
        plan = self.rm.plan(
            env="bt", symbol=s, side=sig.side, entry=sig.entry, stop=sig.stop, score=sig.score, source="auto",
            info=info, account=acct, open_positions=open_like,
            marks=self._close, regime_mult=self.regime.current.risk_mult, taker_fee=self.cfg.taker_fee,
            slippage=self.slip, mmr=mmr, bracket_cap=cap, cls=cls, corr=self._corr, streak=self.loss_streak,
        )
        if not plan.ok:
            self.skipped["limits"] += 1
            return
        self.pending[s] = {"sig": sig, "plan": plan}

    # --- devre kesiciler ---------------------------------------------------
    def _update_risk_state(self, t: int, eq: float) -> None:
        r = self.cfg.risk
        day = t - (t % DAY_MS)
        if self.day_start[0] != day:
            self.day_start = (day, eq)
        week = utc_week_start(t)
        if self.week_start[0] != week:
            self.week_start = (week, eq)
        self.hwm = max(self.hwm, eq)
        self.rm.states["bt"] = RiskState(hwm=self.hwm, last_equity=eq)
        if self.day_start[1] > 0 and (self.day_start[1] - eq) / self.day_start[1] >= r.daily_loss_pct:
            self.halted_until = max(self.halted_until, day + DAY_MS)
        if self.week_start[1] > 0 and (self.week_start[1] - eq) / self.week_start[1] >= r.weekly_loss_pct:
            self.halted_until = max(self.halted_until, week + 7 * DAY_MS)
        if not self.dd_halt and self.hwm > 0 and (self.hwm - eq) / self.hwm >= r.max_drawdown_pct:
            self.dd_halt = True
            from datetime import datetime, timezone
            d = datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            self.warnings.append(f"Maksimum düşüş devre kesicisi {d} tarihinde tetiklendi; sonrasında yeni işlem açılmadı.")

    # --- ana döngü ---------------------------------------------------------
    def run(self) -> dict:
        self._prepare()
        cfg = self.cfg
        total = len(self.timeline)
        last_pct = -1
        for k, T in enumerate(self.timeline):
            T = int(T)
            self._T = T
            if self.cancelled():
                raise BacktestCancelled()
            pct = int(k * 100 / max(1, total))
            if pct != last_pct:
                last_pct = pct
                self.progress(k / max(1, total), f"Simülasyon: %{pct}")
            close_t = T + STEP
            hour_close = close_t % HOUR == 0
            bars_now: dict[str, int] = {}
            for s in self.symbols:
                a = self.data.bars[s]
                p = self.ptr[s]
                n = len(a["t"])
                while p < n and int(a["t"][p]) <= T:
                    p += 1
                self.ptr[s] = p
                if p > 0 and int(a["t"][p - 1]) == T:
                    bars_now[s] = p - 1
                self.view.bufs[(s, "15m")].set_end(p, MAX_BARS_15M)
                self.view.bufs[(s, "1h")].set_end(int(self.h1_count[s][p - 1]) if p > 0 else 0, MAX_BARS_1H)
                if p > 0 and s in bars_now:
                    mi = self.view.marks.get(s)
                    if mi is None:
                        mi = MarkInfo(mark=0.0, index=0.0, funding_rate=FUNDING_DEFAULT, next_funding_time=0, ts=0)
                        self.view.marks[s] = mi
                    c = float(a["c"][p - 1])
                    mi.mark = mi.index = c
                    mi.ts = T
                    if (T // HOUR) != getattr(mi, "_hour", -1):  # funding oranı saatte bir yeterli
                        mi.funding_rate = self._funding_rate_at(s, T)
                        mi._hour = T // HOUR  # type: ignore[attr-defined]
            trading = T >= cfg.start_ms
            if not trading:
                if hour_close:
                    self.regime.update(self.view)  # type: ignore[arg-type]
                continue
            self._total_bars += 1

            # 1) bekleyen girişler bu barın açılışında
            for s in list(self.pending):
                if s in bars_now:
                    self._open_pending(s, bars_now[s])
                else:
                    self.pending.pop(s, None)
            # 2) bar içi: stop / hedef
            for s, p in list(self.positions.items()):
                if s in bars_now:
                    self._process_bar(p, bars_now[s])
            # 3) bar kapanışı: funding, iz süren stop, zaman stopu
            for s, p in list(self.positions.items()):
                f = self.data.funding.get(s)
                if f is not None and len(f[0]):
                    j = self.fptr[s]
                    while j < len(f[0]) and int(f[0][j]) <= T:
                        j += 1
                    while j < len(f[0]) and int(f[0][j]) <= close_t:
                        c = self._close(s) or p.entry
                        amount = -p.sgn * float(f[1][j]) * c * p.qty
                        p.funding += amount
                        self.cash += amount
                        self.funding_total += amount
                        j += 1
                    self.fptr[s] = j
                elif close_t % (8 * HOUR) == 0:
                    c = self._close(s) or p.entry
                    amount = -p.sgn * FUNDING_DEFAULT * c * p.qty
                    p.funding += amount
                    self.cash += amount
                    self.funding_total += amount
                if s in bars_now and s in self.positions:
                    self._manage_on_close(p, bars_now[s], hour_close)
            if self.positions:
                self._exposed_bars += 1
            eq = self._equity()
            if hour_close or k == total - 1:
                self.eq_t.append(close_t // 1000)
                self.eq_v.append(eq)
            self._update_risk_state(close_t, eq)

            # 4) yeni sinyaller (bar kapanışında)
            if hour_close:
                self.regime.update(self.view)  # type: ignore[arg-type]
            if self._entries_blocked(close_t):
                continue
            ctx = StrategyContext(self.view, self.regime.current, cfg.strategies, cfg.scanner, close_t)  # type: ignore[arg-type]
            found = []
            if hour_close and cfg.s2:
                found += self.s2.evaluate_universe([s for s in self.symbols if s in bars_now], ctx)
            for s in bars_now:
                if cfg.s1:
                    sig = self.s1.evaluate(s, ctx)
                    if sig:
                        found.append(sig)
                if cfg.s3:
                    sig = self.s3.evaluate(s, ctx)
                    if sig:
                        found.append(sig)
            for sig in sorted(found, key=lambda x: x.score, reverse=True):
                self._consider(sig, close_t)

        # test sonu: açık pozisyonları son fiyattan kapat
        end_t = int(self.timeline[-1]) + STEP if len(self.timeline) else cfg.end_ms
        for s, p in list(self.positions.items()):
            c = self._close(s) or p.entry
            self._reduce(p, p.qty, c * (1 - p.sgn * self.slip), cfg.taker_fee)
            self._finalize(p, "Test sonu", end_t, self.ptr[s] - 1)
        # Son nokta: kapanış komisyonu ve kayması dahil gerçekleşmiş nakit
        if self.eq_t and self.eq_t[-1] >= end_t // 1000:
            self.eq_v[-1] = self.cash
        else:
            self.eq_t.append(end_t // 1000)
            self.eq_v.append(self.cash)
        self.progress(1.0, "Simülasyon tamamlandı")
        return {
            "trades": self.trades,
            "eq_t": np.array(self.eq_t, dtype=np.int64),
            "eq_v": np.array(self.eq_v, dtype=float),
            "exposure_pct": (self._exposed_bars / self._total_bars * 100.0) if self._total_bars else 0.0,
            "fees_total": self.fees_total,
            "funding_total": self.funding_total,
            "warnings": self.warnings,
            "skipped": self.skipped,
            "symbols_used": self.symbols,
        }
