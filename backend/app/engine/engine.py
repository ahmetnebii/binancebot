"""İşlem motoru: tarama, fırsat yönetimi, otomatik/manuel işlem, pozisyon yönetimi."""
from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal
from typing import Callable, Optional

from ..db import Database
from ..events import EventBus, EventLog
from ..exchange.binance_broker import BinanceBroker
from ..exchange.binance_client import BinanceAPIError, BinanceREST
from ..exchange.broker_base import AccountInfo, Broker, BrokerError, OpenRequest, ReduceEvent, TPOrder
from ..exchange.filters import D, check_qty, floor_step, fmt, stop_price, tp_price
from ..exchange.paper import PaperBroker
from ..features.indicators import atr, correlation, log_returns
from ..features.regime import Regime, RegimeDetector
from ..market.base import MarketData
from ..models import Candle, Position, Signal, SizingPlan, TakeProfit, side_sign
from ..notify.telegram import TelegramNotifier, esc
from ..paths import VERSION
from ..risk.manager import Breaker, RiskManager, loss_streak, open_risk_usdt
from ..risk.sizing import approx_liq_price, symbol_class
from ..settings import SettingsStore
from ..strategies.base import StrategyContext, apply_exit_profile, passes_filters
from ..strategies.mean_reversion import MeanReversion
from ..strategies.lab_rules import LabRule
from ..strategies.patterns import ChartPatterns
from ..strategies.trend_follow import TrendFollow, is_4h_close
from ..strategies.rs_rotation import RSRotation
from ..strategies.scorer import EV_EXEMPT, estimate
from ..strategies.trend_breakout import TrendBreakout
from ..util import floor_time, new_id, now_ms, tf_ms
from .projection import build_projection

log = logging.getLogger(__name__)

SIDE_TR = {"LONG": "Long", "SHORT": "Short"}
CLOSE_REASON = {
    "tp": "Kâr al (TP) hedefleri tamamlandı",
    "sl": "Stop-loss",
    "liquidation": "Likidasyon",
    "adl": "Otomatik kaldıraç azaltma (ADL)",
    "delist": "Delist kapanışı",
    "manual": "Manuel kapanış",
    "other": "Borsa tarafında kapandı",
}
DEFAULT_MANUAL_TPS = [(1.5, 0.4), (3.0, 0.3)]


class EngineError(Exception):
    """Kullanıcıya gösterilecek motor hatası."""


def _px(x: float) -> str:
    if x >= 1000:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:.4f}"
    return f"{x:.6g}"


class Engine:
    def __init__(
        self,
        *,
        db: Database,
        store: SettingsStore,
        bus: EventBus,
        events: EventLog,
        risk: RiskManager,
        notifier: TelegramNotifier,
        market_factory: Callable[[str], MarketData],
        synthetic: bool = False,
    ):
        self.db = db
        self.store = store
        self.bus = bus
        self.events = events
        self.risk = risk
        self.notifier = notifier
        self.market_factory = market_factory
        self.synthetic = synthetic
        self.market: MarketData = market_factory(self.env)
        self.broker: Optional[Broker] = None
        self.broker_state = "starting"
        self.broker_message = ""
        self.account: Optional[AccountInfo] = None
        self.positions: dict[str, Position] = {}
        self.signals: dict[str, Signal] = {}
        self.recent_closed: list[Position] = []
        self.regime_detector = RegimeDetector()
        self.s1 = TrendBreakout()
        self.s2 = RSRotation()
        self.s3 = MeanReversion()
        self.s4 = TrendFollow()
        self.s5 = ChartPatterns()
        self.lab = LabRule()
        self.calibration: dict = {}
        self._last_report_day: Optional[int] = None
        self.scanner: dict = {"last_scan_at": None, "next_scan_at": None, "scanned_symbols": 0}
        self._tasks: list[asyncio.Task] = []
        self._exec_lock = asyncio.Lock()
        self._scan_pending: dict[str, asyncio.Task] = {}
        self._breakers: list[Breaker] = []
        self._active_breakers: set[str] = set()
        self._unknown_positions: set[str] = set()
        self._corr_cache: dict[tuple[str, str], tuple[int, float]] = {}
        self._stats_cache: tuple[int, dict] = (0, {})
        self.watched: dict[tuple[str, str], int] = {}
        self._wire_market()
        self._register_commands()

    # --- özellikler ------------------------------------------------------
    @property
    def env(self) -> str:
        return "paper" if self.synthetic else self.store.connection.env

    @property
    def regime(self) -> Regime:
        return self.regime_detector.current

    def _wire_market(self) -> None:
        self.market.bar_close_handlers.append(self._on_bar_close)
        self.market.kline_listeners.append(self._on_kline)

    # --- yaşam döngüsü ---------------------------------------------------
    async def start(self) -> None:
        await self.risk.load(self.env)
        await self._load_state()
        cal = await self.db.kv_get("calibration")
        self.calibration = (cal or {}).get("strategies", {}) if isinstance(cal, dict) else {}
        self._last_report_day = await self.db.kv_get("daily_report_day")
        if self._last_report_day is None:  # ilk kurulumda boş rapor gönderme
            today = now_ms() - (now_ms() % 86_400_000)
            self._last_report_day = today
            await self.db.kv_set("daily_report_day", today, now_ms())
        await self.market.start()
        await self._init_broker()
        self._tasks = [
            asyncio.create_task(self._tick_loop(), name="tick"),
            asyncio.create_task(self._status_loop(), name="status"),
            asyncio.create_task(self._equity_loop(), name="equity"),
            asyncio.create_task(self._housekeeping_loop(), name="housekeeping"),
            asyncio.create_task(self._reconcile_loop(), name="reconcile"),
            asyncio.create_task(self._initial_scan(), name="initial-scan"),
        ]

    async def stop(self) -> None:
        for t in self._tasks + list(self._scan_pending.values()):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.broker:
            try:
                await self.broker.stop()
            except Exception:  # pragma: no cover
                log.exception("Broker durdurulamadı")
        await self.market.stop()

    async def _load_state(self) -> None:
        env = self.env
        self.positions = {p.id: p for p in await self.db.list_positions(env, "OPEN", 500)}
        self.recent_closed = await self.db.list_positions(env, "CLOSED", 50)
        since = now_ms() - 24 * 3600_000
        self.signals = {s.id: s for s in await self.db.list_signals(since, None, 500)}
        for s in self.signals.values():
            if s.status == "ACTIVE" and s.expires_at < now_ms():
                s.status = "EXPIRED"
                await self.db.save_signal(s)

    async def reload(self, reason: str) -> None:
        """Ortam veya anahtar değişince piyasa verisini ve broker'ı yeniden kurar."""
        async with self._exec_lock:
            if self.store.runtime.auto_mode:
                await self.store.update("runtime", {"auto_mode": False})
            if self.broker:
                try:
                    await self.broker.stop()
                except Exception:
                    log.exception("Broker durdurulamadı")
            self.broker = None
            self.broker_state = "starting"
            new_market = self.market_factory(self.env)
            if new_market is not self.market:
                await self.market.stop()
                self.market = new_market
                self._wire_market()
                self.regime_detector = RegimeDetector()
                await self.market.start()
            for s in self.signals.values():
                if s.status == "ACTIVE":
                    s.status = "EXPIRED"
                    s.status_note = "Ortam değişti"
                    await self.db.save_signal(s)
            await self.risk.load(self.env)
            self.positions = {p.id: p for p in await self.db.list_positions(self.env, "OPEN", 500)}
            self.recent_closed = await self.db.list_positions(self.env, "CLOSED", 50)
            await self._init_broker()
        await self.events.log("info", "system", f"Sistem yeniden yapılandırıldı: {reason}", toast=True, title="Yeniden yapılandırma")
        asyncio.create_task(self._initial_scan())

    async def _init_broker(self) -> None:
        env = self.env
        self.broker_state = "starting"
        self.broker_message = ""
        broker: Broker
        if env == "paper":
            broker = PaperBroker(self.db, self.market, lambda: self.store.paper)
        else:
            conn = self.store.connection
            key = await self.store.get_secret("binance_api_key")
            secret = await self.store.get_secret("binance_api_secret")
            pem = await self.store.get_secret("binance_private_key")
            if not key or (conn.key_type == "hmac" and not secret) or (conn.key_type == "ed25519" and not pem):
                self.broker = None
                self.broker_state = "no_keys"
                self.broker_message = "API anahtarı girilmemiş (Ayarlar → Bağlantı)"
                return
            try:
                rest = BinanceREST(
                    conn.rest_base(),
                    api_key=key,
                    api_secret=secret if conn.key_type == "hmac" else None,
                    private_key_pem=pem if conn.key_type == "ed25519" else None,
                )
            except ValueError as exc:
                self.broker_state = "error"
                self.broker_message = f"Anahtar okunamadı: {exc}"
                return
            broker = BinanceBroker(env, rest, conn.ws_base(), conn.user_stream_path, self.market)
        broker.on_reduce = self._on_reduce
        broker.on_funding = self._on_funding
        broker.on_warning = self._on_broker_warning
        try:
            await broker.start()
        except Exception as exc:
            msg = exc.describe() if isinstance(exc, BinanceAPIError) else str(exc)
            self.broker = None
            self.broker_state = "error"
            self.broker_message = f"Bağlantı kurulamadı: {msg}"
            await self.events.log("error", "broker", f"Broker başlatılamadı: {msg}", toast=True, title="Bağlantı hatası")
            return
        self.broker = broker
        self.broker_state = "ok"
        if isinstance(broker, BinanceBroker):
            for p in self.positions.values():
                broker.register(p.symbol, p.id, p.side)
        try:
            self.account = await broker.account()
            await self.risk.update_equity(env, self.account.equity)
        except Exception as exc:
            log.warning("Hesap bilgisi alınamadı: %s", exc)
        label = {"paper": "Simülasyon", "testnet": "Testnet", "live": "CANLI"}[env]
        await self.events.log("success", "broker", f"{label} ortamı hazır")

    # --- piyasa olayları -------------------------------------------------
    def _on_kline(self, symbol: str, tf: str, candle: Candle) -> None:
        if self.watched.get((symbol, tf)):
            self.bus.publish("kline", {"symbol": symbol, "interval": tf, "candle": candle.to_chart()})

    async def _on_bar_close(self, symbol: str, tf: str, candle: Candle) -> None:
        if tf in ("15m", "1h"):
            key = f"{tf}:{candle.t}"
            if key not in self._scan_pending:
                self._scan_pending[key] = asyncio.create_task(self._delayed_scan(tf, key))
        for p in list(self.positions.values()):
            if p.symbol == symbol and p.timeframe == tf:
                try:
                    await self._manage_on_bar(p)
                except Exception:
                    log.exception("Pozisyon yönetimi hatası (%s)", p.symbol)

    async def _delayed_scan(self, tf: str, key: str) -> None:
        try:
            await asyncio.sleep(4.0)  # tüm sembollerin bar kapanışlarını topla
            await self._scan(tf)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Tarama hatası")
        finally:
            self._scan_pending.pop(key, None)

    async def _initial_scan(self) -> None:
        try:
            await asyncio.wait_for(self.market.ready.wait(), timeout=600)
        except asyncio.TimeoutError:
            return
        await asyncio.sleep(3.0)
        try:
            await self._scan("1h")
            await self._scan("15m")
        except Exception:
            log.exception("İlk tarama hatası")

    async def _scan(self, tf: str) -> None:
        if self.market.state == "down":
            return
        universe = list(self.market.universe)
        if tf == "1h":
            self.regime_detector.update(self.market)
        ctx = StrategyContext(self.market, self.regime, self.store.strategies, self.store.scanner, now_ms())
        found: list[Signal] = []
        t0 = time.perf_counter()
        if tf == "1h" and self.store.strategies.s2_enabled:
            found += self.s2.evaluate_universe(universe, ctx)
        hourly = []
        if tf == "1h" and self.store.strategies.s4_enabled and is_4h_close(ctx.now):
            hourly.append(self.s4)
        if tf == "1h" and self.store.strategies.s5_enabled:
            hourly.append(self.s5)
        lab_rule = self.store.strategies.lab_rule if self.store.strategies.lab_enabled else None
        if lab_rule:
            ltf = lab_rule.get("tf")
            if (ltf == tf) or (ltf == "4h" and tf == "1h" and is_4h_close(ctx.now)):
                hourly.append(self.lab)
        for strat in hourly:
            for sym in universe:
                if sym in self.store.scanner.blacklist:
                    continue
                try:
                    sig = strat.evaluate(sym, ctx)
                except Exception:
                    log.exception("%s değerlendirme hatası (%s)", strat.code, sym)
                    continue
                if sig:
                    found.append(sig)
            await asyncio.sleep(0)
        if tf == "15m":
            per_bar = [s for s, on in ((self.s1, self.store.strategies.s1_enabled), (self.s3, self.store.strategies.s3_enabled)) if on]
            for sym in universe if per_bar else []:
                if sym in self.store.scanner.blacklist:
                    continue
                for strat in per_bar:
                    try:
                        sig = strat.evaluate(sym, ctx)
                    except Exception:
                        log.exception("%s değerlendirme hatası (%s)", strat.code, sym)
                        continue
                    if sig:
                        found.append(sig)
                await asyncio.sleep(0)
        now = now_ms()
        self.scanner = {
            "last_scan_at": now,
            "next_scan_at": floor_time(now, "15m") + tf_ms("15m") + 4000,
            "scanned_symbols": len(universe),
        }
        log.info("Tarama (%s): %d sembol, %d fırsat, %.0f ms", tf, len(universe), len(found), (time.perf_counter() - t0) * 1000)
        st = self.store.strategies
        found = [
            apply_exit_profile(sg, st) for sg in found
            if passes_filters(sg, st, self.regime.name, ctx.funding_rate(sg.symbol))
        ]
        for sig in found:
            await self._on_new_signal(sig)

    # --- fırsatlar -------------------------------------------------------
    def _strategy_stats(self) -> dict:
        ts, cached = self._stats_cache
        if now_ms() - ts < 300_000 and cached:
            return cached
        out: dict[str, dict] = {}
        for p in self.recent_closed:
            if p.r_final is None:
                continue
            d = out.setdefault(p.strategy, {"n": 0, "wins": 0, "win_r": 0.0, "loss_r": 0.0})
            d["n"] += 1
            if p.r_final > 0:
                d["wins"] += 1
                d["win_r"] += p.r_final
            else:
                d["loss_r"] += -p.r_final
        for d in out.values():
            d["win_rate"] = d["wins"] / d["n"] if d["n"] else 0.0
            d["avg_win_r"] = d["win_r"] / d["wins"] if d["wins"] else None
            losses = d["n"] - d["wins"]
            d["avg_loss_r"] = d["loss_r"] / losses if losses else None
        self._stats_cache = (now_ms(), out)
        return out

    async def _fees(self, symbol: str) -> tuple[float, float]:
        if self.broker:
            try:
                return await self.broker.fee_rates(symbol)
            except Exception:
                pass
        return self.store.paper.maker_fee, self.store.paper.taker_fee

    def _slippage(self) -> float:
        return self.store.paper.slippage_bps / 10_000 if self.env == "paper" else 0.0005

    async def _on_new_signal(self, sig: Signal) -> None:
        if any(p.symbol == sig.symbol for p in self.positions.values()):
            return
        for s in self.signals.values():
            if s.status == "ACTIVE" and s.symbol == sig.symbol and s.strategy == sig.strategy:
                return
        await self._enrich_with_oi(sig)
        _, taker = await self._fees(sig.symbol)
        mi = self.market.funding(sig.symbol)
        stats = self._strategy_stats().get(sig.strategy)
        sig.win_prob, sig.ev_r = estimate(
            sig, taker, self._slippage(), mi.funding_rate if mi else 0.0, stats, self.calibration
        )
        if sig.strategy in self.calibration:
            sig.features["calibrated"] = 1.0
        if sig.ev_r < self.store.risk.min_ev_r and sig.strategy not in EV_EXEMPT:
            sig.warnings.append("Tahmini beklenti eşiğin altında: otomatik mod bu fırsatı işlemez")
        sig.price = self.market.price(sig.symbol)
        sig.plan = await self._plan(sig.symbol, sig.side, sig.price or sig.entry, sig.stop, sig.score, "auto")
        self.signals[sig.id] = sig
        await self.db.save_signal(sig)
        self.bus.publish("signal", sig.model_dump(mode="json"))
        await self.events.log(
            "info", "signal",
            f"Yeni fırsat: {sig.symbol} {SIDE_TR[sig.side]} — {sig.strategy_name} (puan {sig.score:.0f}, EV {sig.ev_r:+.2f}R)",
        )
        auto = self.store.runtime.auto_mode
        if auto:
            ok, why = self._auto_allowed(sig)
            if ok:
                try:
                    await self.execute_signal(sig.id, source="auto")
                except EngineError as exc:
                    sig.status_note = f"Otomatik işlenemedi: {exc}"
                    await self.db.save_signal(sig)
                    self.bus.publish("signal", sig.model_dump(mode="json"))
                    await self.events.log("warning", "auto", f"{sig.symbol}: otomatik işlem açılamadı — {exc}")
            else:
                sig.status_note = f"Otomatik mod işlemedi: {why}"
                await self.db.save_signal(sig)
                self.bus.publish("signal", sig.model_dump(mode="json"))
        else:
            self.notifier.notify(
                f"🔔 <b>Yeni fırsat</b> · {esc(sig.symbol)} {SIDE_TR[sig.side]}\n"
                f"{esc(sig.strategy_name)} · puan {sig.score:.0f} · EV {sig.ev_r:+.2f}R\n"
                f"Giriş {_px(sig.entry)} · Stop {_px(sig.stop)}\n"
                f"Onay için panele girin (otomatik mod kapalı).",
                kind="signal",
            )

    async def _enrich_with_oi(self, sig: Signal) -> None:
        """Open interest teyidi: kırılım/momentumda artan OI yeni pozisyon girişini gösterir.

        Ortalamaya dönüşte (S3) yorum tersine döner: azalan OI (pozisyon kapanışı/likidasyon)
        dönüşü destekler, artan OI ise trendin devam edebileceğine işaret eder.
        """
        if not self.store.strategies.oi_confirmation:
            return
        try:
            oi = await self.market.oi_change_pct(sig.symbol, 60)
        except Exception:
            oi = None
        if oi is None:
            return
        sig.features["oi_change_1h"] = round(oi, 3)
        supportive = oi >= 1.0 if sig.strategy != "S3" else oi <= -1.0
        against = oi <= -1.0 if sig.strategy != "S3" else oi >= 1.0
        if supportive:
            sig.score = round(min(100.0, sig.score + 5.0), 1)
            what = "yeni pozisyon girişi hareketi destekliyor" if sig.strategy != "S3" else "pozisyonlar kapanıyor, dönüş ihtimali artıyor"
            sig.reasons.append(f"Open interest son 1 saatte %{oi:+.1f}: {what}")
        elif against:
            sig.score = round(max(0.0, sig.score - 5.0), 1)
            what = "hareket pozisyon kapanışından kaynaklanıyor olabilir" if sig.strategy != "S3" else "yeni pozisyonlar trendi sürdürebilir"
            sig.warnings.append(f"Open interest %{oi:+.1f}: {what}")

    def set_calibration(self, strategies: dict) -> None:
        self.calibration = dict(strategies or {})
        self._stats_cache = (0, {})

    async def _check_strategy_health(self, strategy: str) -> None:
        """Son N işlemin beklentisi eşiğin altına düşen stratejiyi otomatik kapatır."""
        cfg = self.store.strategies
        key = {"S1": "s1_enabled", "S2": "s2_enabled", "S3": "s3_enabled", "S4": "s4_enabled", "S5": "s5_enabled", "S6": "lab_enabled"}.get(strategy)
        if not cfg.health_monitor or key is None or not getattr(cfg, key):
            return
        closed = [
            p for p in await self.db.list_positions(self.env, "CLOSED", 1000)
            if p.strategy == strategy and p.r_final is not None and p.close_reason != "Simülasyon sıfırlandı"
        ][: cfg.health_min_trades]
        if len(closed) < cfg.health_min_trades:
            return
        exp_r = sum(p.r_final or 0.0 for p in closed) / len(closed)
        if exp_r >= cfg.health_min_expectancy_r:
            return
        await self.store.update("strategies", {key: False})
        name = closed[0].strategy_name
        msg = (
            f"{name} ({strategy}) otomatik olarak kapatıldı: son {len(closed)} işlemin beklentisi "
            f"{exp_r:+.2f}R (eşik {cfg.health_min_expectancy_r:+.2f}R). Backtest ile yeniden değerlendirin."
        )
        await self.events.log("warning", "health", msg, toast=True, title="Strateji sağlık monitörü")
        self.notifier.notify(f"🩺 <b>Strateji kapatıldı</b>\n{esc(msg)}", kind="system")

    async def _maybe_daily_report(self) -> None:
        """UTC 00:05'ten sonra bir önceki günün özetini Telegram'a gönderir (günde bir kez)."""
        if not self.store.telegram.daily_report:
            return
        now = now_ms()
        day = now - (now % 86_400_000)
        if now - day < 5 * 60_000 or self._last_report_day == day:
            return
        self._last_report_day = day
        await self.db.kv_set("daily_report_day", day, now)
        prev = day - 86_400_000
        closed = [
            p for p in await self.db.list_positions(self.env, "CLOSED", 500)
            if p.closed_at and prev <= p.closed_at < day and p.close_reason != "Simülasyon sıfırlandı"
        ]
        nets = [p.realized_pnl - p.fees + p.funding for p in closed]
        wins = sum(1 for x in nets if x > 0)
        eq_rows = await self.db.list_equity(self.env, prev)
        eq_rows = [r for r in eq_rows if r[0] < day]
        start_eq = eq_rows[0][1] if eq_rows else None
        end_eq = eq_rows[-1][1] if eq_rows else (self.account.equity if self.account else None)
        lines = [f"📊 <b>Günlük rapor</b> · {self.env}"]
        if start_eq and end_eq:
            lines.append(f"Özsermaye: {end_eq:.2f} USDT ({(end_eq / start_eq - 1) * 100:+.2f}%)")
        lines.append(f"Kapanan işlem: {len(closed)} · kazanan {wins} · K/Z {sum(nets):+.2f} USDT")
        if closed:
            rs = [p.r_final for p in closed if p.r_final is not None]
            if rs:
                lines.append(f"Ortalama R: {sum(rs) / len(rs):+.2f} · en iyi {max(rs):+.2f}R · en kötü {min(rs):+.2f}R")
        lines.append(f"Açık pozisyon: {len(self.positions)} · otomatik mod: {'AÇIK' if self.store.runtime.auto_mode else 'kapalı'}")
        self.notifier.notify("\n".join(lines), kind="system")

    def _blocking_breakers(self) -> list[Breaker]:
        return [b for b in self._breakers if b.active and b.blocks_auto]

    def _auto_allowed(self, sig: Signal) -> tuple[bool, str]:
        if self.store.runtime.paused:
            return False, "sistem durduruldu"
        blocking = self._blocking_breakers()
        if blocking:
            return False, f"devre kesici aktif ({blocking[0].label})"
        if self.broker is None:
            return False, "broker hazır değil"
        if sig.plan is None or not sig.plan.ok:
            return False, sig.plan.reason if sig.plan else "plan yok"
        if sig.ev_r < self.store.risk.min_ev_r and sig.strategy not in EV_EXEMPT:
            return False, "beklenti eşiğin altında"
        if self.regime.name == "CHAOS":
            return False, "aşırı oynaklık rejimi"
        return True, ""

    def _corr(self, a: str, b: str) -> float:
        if a == b:
            return 1.0
        key = (a, b) if a < b else (b, a)
        cached = self._corr_cache.get(key)
        if cached and now_ms() - cached[0] < 3600_000:
            return cached[1]
        ba, bb = self.market.buffer(a, "1h"), self.market.buffer(b, "1h")
        value = 0.0
        if ba is not None and bb is not None and len(ba) > 80 and len(bb) > 80:
            value = correlation(log_returns(ba.arrays()["c"][-73:]), log_returns(bb.arrays()["c"][-73:]))
        self._corr_cache[key] = (now_ms(), value)
        return value

    async def _account(self) -> AccountInfo:
        if self.broker is None:
            raise EngineError(self.broker_message or "Broker hazır değil")
        if self.broker.kind == "paper" or self.account is None:
            self.account = await self.broker.account()
        return self.account

    async def _plan(
        self,
        symbol: str,
        side: str,
        entry: float,
        stop: float,
        score: float,
        source: str,
        risk_pct: Optional[float] = None,
        leverage: Optional[int] = None,
    ) -> SizingPlan:
        if self.broker is None:
            return SizingPlan(ok=False, reason=self.broker_message or "Broker hazır değil")
        info = self.market.symbols.get(symbol)
        if info is None:
            return SizingPlan(ok=False, reason=f"{symbol} sembol bilgisi yok")
        try:
            account = await self._account()
        except Exception as exc:
            return SizingPlan(ok=False, reason=f"Hesap bilgisi alınamadı: {exc}")
        _, taker = await self._fees(symbol)
        cls = symbol_class(symbol, self.market.volume_rank(symbol))
        d = abs(entry - stop) / entry if entry > 0 else 0.01
        guess = account.equity * self.store.risk.base_risk_pct / max(d, 1e-4)
        try:
            mmr, bracket_cap = await self.broker.mmr_and_cap(symbol, guess, cls)
        except Exception:
            mmr, bracket_cap = 0.01, 20
        streak, _ = loss_streak(self.recent_closed)
        return self.risk.plan(
            env=self.env, symbol=symbol, side=side, entry=entry, stop=stop, score=score, source=source,
            info=info, account=account, open_positions=list(self.positions.values()),
            marks=self.market.mark, regime_mult=self.regime.risk_mult, taker_fee=taker,
            slippage=self._slippage(), mmr=mmr, bracket_cap=bracket_cap, cls=cls, corr=self._corr,
            streak=streak, risk_pct_override=risk_pct, leverage_override=leverage,
        )

    # --- işlem açma ------------------------------------------------------
    async def execute_signal(
        self, signal_id: str, source: str = "manual", risk_pct: Optional[float] = None, leverage: Optional[int] = None
    ) -> Position:
        async with self._exec_lock:
            sig = self.signals.get(signal_id) or await self.db.get_signal(signal_id)
            if sig is None:
                raise EngineError("Fırsat bulunamadı")
            if sig.status != "ACTIVE":
                raise EngineError("Bu fırsat artık aktif değil")
            if now_ms() > sig.expires_at:
                sig.status = "EXPIRED"
                await self._save_signal(sig)
                raise EngineError("Fırsatın süresi doldu")
            if source == "auto":
                ok, why = self._auto_allowed(sig)
                if not ok:
                    raise EngineError(why)
            price = self.market.price(sig.symbol)
            if not price:
                raise EngineError("Anlık fiyat alınamadı")
            sgn = side_sign(sig.side)
            if sgn * (price - sig.stop) <= 0:
                sig.status = "REJECTED"
                sig.status_note = "Fiyat stop seviyesini geçti"
                await self._save_signal(sig)
                raise EngineError("Fiyat stop seviyesini geçmiş; fırsat geçersiz")
            risk_unit = abs(sig.entry - sig.stop)
            drift_r = sgn * (price - sig.entry) / risk_unit if risk_unit > 0 else 0.0
            limit = self.store.risk.max_entry_drift_r
            if drift_r > limit:
                raise EngineError(f"Fiyat sinyal seviyesinden {drift_r:.2f}R uzaklaştı (sınır {limit:.2f}R); kovalanmıyor")
            pos = await self._open(
                symbol=sig.symbol, side=sig.side, strategy=sig.strategy, strategy_name=sig.strategy_name,
                timeframe=sig.timeframe, signal_id=sig.id, source=source, price=price, stop=sig.stop,
                tp_spec=[(tp.r, tp.fraction) for tp in sig.take_profits], trailing=sig.trailing_atr_mult,
                time_stop_bars=sig.time_stop_bars, atr_value=sig.atr, score=sig.score,
                risk_pct=risk_pct, leverage=leverage, plan_source="auto" if source == "auto" else "signal",
            )
            sig.status = "EXECUTED"
            sig.position_id = pos.id
            sig.status_note = "Otomatik mod tarafından işlendi" if source == "auto" else "Manuel olarak işlendi"
            await self._save_signal(sig)
            return pos

    async def preview_signal(self, signal_id: str, risk_pct: Optional[float], leverage: Optional[int]) -> dict:
        """Fırsatı işlemeden önce güncel fiyatla plan (işleme ile aynı kurallar)."""
        sig = self.signals.get(signal_id) or await self.db.get_signal(signal_id)
        if sig is None:
            raise EngineError("Fırsat bulunamadı")
        price = self.market.price(sig.symbol)
        if not price:
            raise EngineError("Anlık fiyat alınamadı")
        sgn = side_sign(sig.side)
        plan = await self._plan(sig.symbol, sig.side, price, sig.stop, sig.score, "signal", risk_pct, leverage)
        risk_unit = abs(price - sig.stop)
        drift_r = sgn * (price - sig.entry) / abs(sig.entry - sig.stop) if sig.entry != sig.stop else 0.0
        if sgn * (price - sig.stop) <= 0:
            plan = SizingPlan(ok=False, reason="Fiyat stop seviyesini geçmiş; fırsat geçersiz")
        elif drift_r > self.store.risk.max_entry_drift_r:
            plan.ok = False
            plan.reason = f"Fiyat sinyal seviyesinden {drift_r:.2f}R uzaklaştı (sınır {self.store.risk.max_entry_drift_r:.2f}R)"
        tps = [TakeProfit(r=t.r, price=price + sgn * t.r * risk_unit, fraction=t.fraction) for t in sig.take_profits]
        return {"plan": plan.model_dump(mode="json"), "entry": price, "take_profits": [t.model_dump() for t in tps]}

    async def preview_manual(
        self, symbol: str, side: str, stop: float, tps: Optional[list[dict]], risk_pct: Optional[float], leverage: Optional[int]
    ) -> dict:
        price = self.market.price(symbol)
        if not price:
            raise EngineError(f"{symbol} için fiyat yok")
        plan = await self._plan(symbol, side, price, stop, 75.0, "manual", risk_pct, leverage)
        spec = self._tp_spec(side, price, stop, tps)
        risk_unit = abs(price - stop)
        sgn = side_sign(side)
        tp_models = [TakeProfit(r=r, price=price + sgn * r * risk_unit, fraction=f) for r, f in spec]
        return {"plan": plan.model_dump(mode="json"), "entry": price, "take_profits": [t.model_dump() for t in tp_models]}

    def _tp_spec(self, side: str, price: float, stop: float, tps: Optional[list[dict]]) -> list[tuple[float, float]]:
        if not tps:
            return list(DEFAULT_MANUAL_TPS)
        risk_unit = abs(price - stop)
        sgn = side_sign(side)
        out: list[tuple[float, float]] = []
        total = 0.0
        for t in tps:
            frac = float(t.get("fraction", 0) or 0)
            if frac <= 0:
                continue
            if t.get("price"):
                r = sgn * (float(t["price"]) - price) / risk_unit if risk_unit > 0 else 0.0
            else:
                r = float(t.get("r", 0) or 0)
            if r <= 0:
                raise EngineError("Kâr hedefi giriş fiyatının doğru tarafında olmalı")
            total += frac
            out.append((round(r, 4), frac))
        if total > 1.0001:
            raise EngineError("Hedef oranlarının toplamı %100'ü geçemez")
        return sorted(out, key=lambda x: x[0])

    async def manual_order(
        self, symbol: str, side: str, stop: float, tps: Optional[list[dict]], risk_pct: Optional[float], leverage: Optional[int]
    ) -> Position:
        async with self._exec_lock:
            symbol = symbol.upper()
            if symbol not in self.market.symbols:
                raise EngineError("Sembol bulunamadı")
            if any(p.symbol == symbol for p in self.positions.values()):
                raise EngineError("Bu sembolde zaten açık pozisyon var")
            price = self.market.price(symbol)
            if not price:
                raise EngineError("Anlık fiyat alınamadı")
            buf = self.market.buffer(symbol, "15m")
            at = 0.0
            if buf is not None and len(buf) > 20:
                a = buf.arrays()
                at = float(atr(a["h"], a["l"], a["c"], 14)[-1])
            return await self._open(
                symbol=symbol, side=side, strategy="M", strategy_name="Manuel işlem", timeframe="15m",
                signal_id=None, source="manual", price=price, stop=stop, tp_spec=self._tp_spec(side, price, stop, tps),
                trailing=3.0 if at > 0 else None, time_stop_bars=0, atr_value=at, score=75.0,
                risk_pct=risk_pct, leverage=leverage,
            )

    async def _open(
        self,
        *,
        symbol: str,
        side: str,
        strategy: str,
        strategy_name: str,
        timeframe: str,
        signal_id: Optional[str],
        source: str,
        price: float,
        stop: float,
        tp_spec: list[tuple[float, float]],
        trailing: Optional[float],
        time_stop_bars: int,
        atr_value: float,
        score: float,
        risk_pct: Optional[float],
        leverage: Optional[int],
        plan_source: Optional[str] = None,
    ) -> Position:
        if self.broker is None:
            raise EngineError(self.broker_message or "Broker hazır değil")
        if any(p.symbol == symbol for p in self.positions.values()):
            raise EngineError("Bu sembolde zaten açık pozisyon var")
        plan = await self._plan(symbol, side, price, stop, score, plan_source or source, risk_pct, leverage)
        if not plan.ok:
            raise EngineError(plan.reason)
        book = await self.market.book(symbol)
        if book:
            bid, ask = book
            mid = (bid + ask) / 2.0
            spread_bps = (ask - bid) / mid * 10_000 if mid > 0 else 0.0
            limit_bps = self.store.scanner.max_spread_bps
            if spread_bps > limit_bps:
                raise EngineError(
                    f"Spread çok yüksek ({spread_bps:.1f} bps, sınır {limit_bps:.1f} bps): kayma riski nedeniyle işlem açılmadı"
                )
        info = self.market.symbols[symbol]
        sgn = side_sign(side)
        qty = D(plan.qty)
        stop_d = stop_price(side, stop, info.tick_size)
        risk_unit = abs(price - float(stop_d))
        tps: list[TPOrder] = []
        tp_models: list[TakeProfit] = []
        for i, (r, frac) in enumerate(tp_spec, start=1):
            px = tp_price(side, price + sgn * r * risk_unit, info.tick_size)
            q = floor_step(qty * D(frac), info.step_size)
            if check_qty(info, q, float(px), market=False, reduce_only=True):
                continue
            tps.append(TPOrder(len(tps) + 1, px, q))
            tp_models.append(TakeProfit(r=r, price=float(px), fraction=frac))
        pid = new_id("pos")
        req = OpenRequest(
            position_id=pid, symbol=symbol, side=side, qty=qty, leverage=plan.leverage,  # type: ignore[arg-type]
            ref_price=price, stop_price=stop_d, tps=tps,
            entry_mode="market" if self.env == "testnet" else "ioc", max_slippage_pct=0.002, mmr=plan.mmr or 0.01,
        )
        try:
            fill = await self.broker.open(req)
        except BrokerError as exc:
            await self.events.log("error", "trade", f"{symbol}: emir başarısız — {exc}", toast=True, title="Emir başarısız")
            raise EngineError(str(exc)) from exc
        except Exception as exc:
            log.exception("Emir hatası")
            await self.events.log("error", "trade", f"{symbol}: beklenmeyen emir hatası — {exc}", toast=True, title="Emir hatası")
            raise EngineError(f"Beklenmeyen emir hatası: {exc}") from exc
        now = now_ms()
        entry = fill.price
        liq = approx_liq_price(side, entry, plan.leverage, plan.mmr or 0.01)
        if self.broker.kind != "paper":
            try:
                bp = (await self.broker.positions()).get(symbol)
                if bp and bp.liq_price > 0:
                    liq = bp.liq_price
            except Exception:
                pass
        notional = entry * fill.qty
        pos = Position(
            id=pid, env=self.env, symbol=symbol, side=side, strategy=strategy,  # type: ignore[arg-type]
            strategy_name=strategy_name, signal_id=signal_id, source=source, opened_at=now,  # type: ignore[arg-type]
            timeframe=timeframe, entry_price=entry, qty=fill.qty, initial_qty=fill.qty, leverage=plan.leverage,
            margin=notional / plan.leverage, notional=notional, stop_price=float(stop_d), initial_stop=float(stop_d),
            take_profits=tp_models, trailing_atr_mult=trailing, atr=atr_value, liq_price=liq,
            time_stop_at=(now + time_stop_bars * tf_ms(timeframe)) if time_stop_bars else None,
            fees=fill.fee, risk_usdt=abs(entry - float(stop_d)) * fill.qty, best_price=entry, worst_price=entry,
        )
        pos.add_event(
            now, "open",
            f"{SIDE_TR[side]} açıldı: {fill.qty:g} @ {_px(entry)} · {plan.leverage}x · stop {_px(float(stop_d))}",
            entry, fill.qty,
        )
        pos.refresh_runtime(self.market.mark(symbol) or entry)
        self.positions[pid] = pos
        await self._save_position(pos)
        src = "otomatik" if source == "auto" else "manuel"
        await self.events.log(
            "success", "trade",
            f"{symbol} {SIDE_TR[side]} açıldı ({src}) — {plan.leverage}x, marjin {pos.margin:.2f} USDT, risk {pos.risk_usdt:.2f} USDT",
            toast=True, title="Pozisyon açıldı",
        )
        self.notifier.notify(
            f"🟢 <b>Pozisyon açıldı</b> · {esc(symbol)} {SIDE_TR[side]} ({src})\n"
            f"Giriş {_px(entry)} · {plan.leverage}x · marjin {pos.margin:.2f} USDT\n"
            f"Stop {_px(pos.stop_price)} · risk {pos.risk_usdt:.2f} USDT"
            + "".join(f"\nTP{i} {_px(t.price)}" for i, t in enumerate(tp_models, start=1)),
            kind="trade",
        )
        return pos

    # --- pozisyon azaltma/kapama -----------------------------------------
    def _find_open(self, symbol: str) -> Optional[Position]:
        for p in self.positions.values():
            if p.symbol == symbol and p.status == "OPEN":
                return p
        return None

    async def _on_reduce(self, ev: ReduceEvent) -> None:
        pos = self._find_open(ev.symbol)
        if pos is None:
            await self.events.log("warning", "trade", f"{ev.symbol}: botun kaydında olmayan kapanış ({ev.kind})")
            return
        await self._apply_reduce(pos, ev.qty, ev.price, ev.fee, ev.realized_pnl, ev.kind, ev.tp_index, ev.ts)

    async def _apply_reduce(
        self,
        pos: Position,
        qty: float,
        price: float,
        fee: float,
        pnl: Optional[float],
        kind: str,
        tp_index: Optional[int] = None,
        ts: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> None:
        if pos.status != "OPEN":
            return
        ts = ts or now_ms()
        q = min(qty, pos.qty)
        realized = pnl if pnl is not None else pos.sign * (price - pos.entry_price) * q
        pos.qty = max(0.0, pos.qty - q)
        pos.realized_pnl += realized
        pos.fees += fee
        pos.exit_qty += q
        pos.exit_value += q * price
        if kind == "tp":
            idx = (tp_index or 1) - 1
            if 0 <= idx < len(pos.take_profits):
                pos.take_profits[idx].filled = True
                pos.take_profits[idx].filled_at = ts
            pos.add_event(ts, "tp", f"TP{idx + 1} doldu: {q:g} @ {_px(price)} (K/Z {realized:+.2f} USDT)", price, q)
        elif kind == "sl":
            pos.add_event(ts, "sl", f"Stop tetiklendi: {q:g} @ {_px(price)} (K/Z {realized:+.2f} USDT)", price, q)
        elif kind == "liquidation":
            pos.add_event(ts, "warning", f"LİKİDASYON: {q:g} @ {_px(price)}", price, q)
        elif kind == "manual":
            word = "Kapatıldı" if pos.qty <= 0 else "Kısmi kapanış"
            pos.add_event(ts, "close" if pos.qty <= 0 else "partial_close", f"{word}: {q:g} @ {_px(price)} (K/Z {realized:+.2f} USDT)", price, q)
        else:
            pos.add_event(ts, "close", f"{CLOSE_REASON.get(kind, kind)}: {q:g} @ {_px(price)}", price, q)
        info = self.market.symbols.get(pos.symbol)
        min_q = float(info.min_qty) if info else 0.0
        if pos.qty <= max(1e-12, min_q * 0.5):
            await self._finalize(pos, reason or CLOSE_REASON.get(kind, kind), price)
            return
        if kind == "tp":
            if not pos.breakeven_done:
                try:
                    await self._move_to_breakeven(pos, "TP sonrası stop başa-başa çekildi")
                except EngineError as exc:
                    pos.add_event(ts, "warning", f"Başa-baş stop yapılamadı: {exc}")
            if pos.trailing_atr_mult:
                pos.trailing_active = True
        await self._save_position(pos)

    async def _on_funding(self, symbol: str, amount: float, ts: int) -> None:
        pos = self._find_open(symbol)
        if pos is None:
            return
        pos.funding += amount
        pos.add_event(ts, "funding", f"Funding {'alındı' if amount >= 0 else 'ödendi'}: {amount:+.4f} USDT")
        await self._save_position(pos)

    async def _on_broker_warning(self, message: str) -> None:
        await self.events.log("warning", "broker", message, toast=True, title="Broker uyarısı")
        self.notifier.notify(f"⚠️ {esc(message)}", kind="system")

    async def _finalize(self, pos: Position, reason: str, last_price: Optional[float] = None) -> None:
        if pos.status == "CLOSED":
            return
        now = now_ms()
        pos.status = "CLOSED"
        pos.closed_at = now
        pos.exit_price = (pos.exit_value / pos.exit_qty) if pos.exit_qty > 0 else (last_price or pos.mark_price)
        pos.close_reason = reason
        pos.qty = 0.0
        net = pos.realized_pnl - pos.fees + pos.funding
        pos.r_final = round(net / pos.risk_usdt, 4) if pos.risk_usdt > 0 else None
        pos.refresh_runtime(pos.exit_price or pos.entry_price)
        self.positions.pop(pos.id, None)
        self.recent_closed.insert(0, pos)
        self.recent_closed = self.recent_closed[:50]
        self._stats_cache = (0, {})
        await self._save_position(pos)
        if self.broker:
            try:
                await self.broker.cleanup(pos.symbol)
            except Exception:
                log.warning("Emir temizliği başarısız (%s)", pos.symbol)
        r_txt = f"{pos.r_final:+.2f}R" if pos.r_final is not None else "—"
        await self.events.log(
            "success" if net >= 0 else "warning", "trade",
            f"{pos.symbol} kapandı — {reason} · K/Z {net:+.2f} USDT ({r_txt})",
            toast=True, title="Pozisyon kapandı",
        )
        self.notifier.notify(
            f"{'✅' if net >= 0 else '🔻'} <b>Pozisyon kapandı</b> · {esc(pos.symbol)} {SIDE_TR[pos.side]}\n"
            f"{esc(reason)} · K/Z {net:+.2f} USDT ({r_txt})",
            kind="trade",
        )
        try:
            await self._check_strategy_health(pos.strategy)
        except Exception:
            log.exception("Strateji sağlık kontrolü hatası")

    async def close_position(self, pos_id: str, fraction: float = 1.0, reason: str = "Manuel kapanış") -> Position:
        async with self._exec_lock:
            return await self._close_internal(pos_id, fraction, reason)

    async def _close_internal(self, pos_id: str, fraction: float, reason: str) -> Position:
        pos = self.positions.get(pos_id)
        if pos is None:
            raise EngineError("Açık pozisyon bulunamadı")
        if self.broker is None:
            raise EngineError(self.broker_message or "Broker hazır değil")
        info = self.market.symbols.get(pos.symbol)
        qty: Optional[Decimal] = None
        if fraction < 0.999 and info is not None:
            qty = floor_step(D(pos.qty) * D(fraction), info.step_size)
            if qty <= 0 or qty < D(info.min_qty):
                raise EngineError("Kapatılacak miktar borsanın minimumunun altında")
            if D(pos.qty) - qty < D(info.min_qty):
                qty = None
        try:
            fill = await self.broker.close(pos.symbol, pos.side, qty, reason)
        except BrokerError as exc:
            raise EngineError(str(exc)) from exc
        if fill is None:
            await self._finalize(pos, "Borsada pozisyon bulunamadı")
            return pos
        await self._apply_reduce(pos, fill.qty, fill.price, fill.fee, fill.realized_pnl, "manual", reason=reason)
        return pos

    async def close_all(self) -> int:
        n = 0
        for pid in list(self.positions.keys()):
            try:
                await self.close_position(pid, 1.0, "Tümünü kapat komutu")
                n += 1
            except EngineError as exc:
                await self.events.log("error", "trade", f"Kapatılamadı: {exc}")
        return n

    # --- stop / hedef düzenleme -------------------------------------------
    async def _set_stop(self, pos: Position, price: float, kind: str, message: str) -> None:
        if self.broker is None:
            raise EngineError(self.broker_message or "Broker hazır değil")
        mark = self.market.mark(pos.symbol) or pos.mark_price or pos.entry_price
        if pos.side == "LONG" and price >= mark:
            raise EngineError("Long pozisyonda stop, mevcut fiyatın altında olmalı")
        if pos.side == "SHORT" and price <= mark:
            raise EngineError("Short pozisyonda stop, mevcut fiyatın üstünde olmalı")
        info = self.market.symbols.get(pos.symbol)
        new_stop = stop_price(pos.side, price, info.tick_size if info else "0.0001")
        try:
            await self.broker.replace_stop(pos.symbol, pos.side, new_stop)
        except BrokerError as exc:
            raise EngineError(str(exc)) from exc
        old = pos.stop_price
        pos.stop_price = float(new_stop)
        pos.add_event(now_ms(), kind, f"{message}: {_px(old)} → {_px(pos.stop_price)}", pos.stop_price)
        await self._save_position(pos)

    async def _move_to_breakeven(self, pos: Position, message: str) -> None:
        _, taker = await self._fees(pos.symbol)
        be = pos.entry_price * (1 + pos.sign * 2 * taker)
        mark = self.market.mark(pos.symbol) or pos.mark_price or pos.entry_price
        if pos.sign * (mark - be) <= 0:
            raise EngineError("Fiyat henüz başa-baş seviyesini (giriş + komisyon) geçmedi; stop oraya taşınamaz")
        await self._set_stop(pos, be, "breakeven", message)
        pos.breakeven_done = True
        await self._save_position(pos)

    async def move_stop(self, pos_id: str, price: float) -> Position:
        async with self._exec_lock:
            pos = self.positions.get(pos_id)
            if pos is None:
                raise EngineError("Açık pozisyon bulunamadı")
            await self._set_stop(pos, price, "stop_move", "Stop elle taşındı")
            return pos

    async def breakeven(self, pos_id: str) -> Position:
        async with self._exec_lock:
            pos = self.positions.get(pos_id)
            if pos is None:
                raise EngineError("Açık pozisyon bulunamadı")
            await self._move_to_breakeven(pos, "Stop başa-başa çekildi")
            return pos

    async def set_take_profits(self, pos_id: str, items: list[dict]) -> Position:
        async with self._exec_lock:
            pos = self.positions.get(pos_id)
            if pos is None:
                raise EngineError("Açık pozisyon bulunamadı")
            if self.broker is None:
                raise EngineError(self.broker_message or "Broker hazır değil")
            info = self.market.symbols[pos.symbol]
            mark = self.market.mark(pos.symbol) or pos.entry_price
            filled = [t for t in pos.take_profits if t.filled]
            remaining_frac = max(0.0, 1.0 - sum(t.fraction for t in filled))
            total = sum(float(i.get("fraction", 0) or 0) for i in items)
            if total > remaining_frac + 1e-6:
                raise EngineError(f"Hedef oranlarının toplamı kalan pozisyonu (%{remaining_frac * 100:.0f}) aşıyor")
            new_models: list[TakeProfit] = []
            orders: list[TPOrder] = []
            rpu = pos.risk_per_unit
            for i in sorted(items, key=lambda x: pos.sign * float(x["price"])):
                px = float(i["price"])
                frac = float(i.get("fraction", 0) or 0)
                if frac <= 0:
                    continue
                if pos.sign * (px - mark) <= 0:
                    raise EngineError("Kâr hedefi mevcut fiyatın kâr tarafında olmalı")
                q = min(floor_step(D(pos.initial_qty) * D(frac), info.step_size), floor_step(D(pos.qty), info.step_size))
                if check_qty(info, q, px, market=False, reduce_only=True):
                    raise EngineError("Hedef miktarı borsanın minimumunun altında")
                tpx = tp_price(pos.side, px, info.tick_size)
                r = pos.sign * (float(tpx) - pos.entry_price) / rpu if rpu > 0 else 0.0
                new_models.append(TakeProfit(r=round(r, 3), price=float(tpx), fraction=frac))
                orders.append(TPOrder(len(filled) + len(orders) + 1, tpx, q))
            try:
                await self.broker.replace_tps(pos.symbol, pos.side, orders)
            except BrokerError as exc:
                raise EngineError(str(exc)) from exc
            pos.take_profits = filled + new_models
            pos.add_event(now_ms(), "info", "Kâr hedefleri güncellendi: " + ", ".join(_px(t.price) for t in new_models))
            await self._save_position(pos)
            return pos

    async def _manage_on_bar(self, pos: Position) -> None:
        buf = self.market.buffer(pos.symbol, pos.timeframe)
        if buf is None or len(buf) < 20:
            return
        a = buf.arrays()
        at = float(atr(a["h"], a["l"], a["c"], 14)[-1])
        mark = self.market.mark(pos.symbol)
        if pos.trailing_active and pos.trailing_atr_mult and at > 0 and mark:
            mask = a["t"] >= floor_time(pos.opened_at, pos.timeframe)
            if pos.side == "LONG":
                ext = max(float(a["h"][mask].max()) if mask.any() else pos.best_price, pos.best_price)
                cand = ext - pos.trailing_atr_mult * at
                if cand > pos.stop_price + 0.25 * at and cand < mark - 0.1 * at:
                    async with self._exec_lock:
                        await self._set_stop(pos, cand, "trailing", "İz süren stop güncellendi")
            else:
                ext = min(float(a["l"][mask].min()) if mask.any() else pos.best_price, pos.best_price)
                cand = ext + pos.trailing_atr_mult * at
                if cand < pos.stop_price - 0.25 * at and cand > mark + 0.1 * at:
                    async with self._exec_lock:
                        await self._set_stop(pos, cand, "trailing", "İz süren stop güncellendi")
        if pos.time_stop_at and now_ms() >= pos.time_stop_at and pos.mfe_r < 0.5 and not any(t.filled for t in pos.take_profits):
            async with self._exec_lock:
                if pos.id in self.positions:
                    await self._close_internal(pos.id, 1.0, "Zaman stopu (beklenen ilerleme olmadı)")

    # --- döngüler --------------------------------------------------------
    async def _tick_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(1.0)
                ts = now_ms()
                for p in list(self.positions.values()):
                    mark = self.market.mark(p.symbol)
                    last = self.market.price(p.symbol)
                    if self.broker is not None and self.broker.kind == "paper":
                        await self.broker.on_price(p.symbol, last, mark, ts)
                    if p.id in self.positions:
                        p.refresh_runtime(mark or last)
                self.bus.publish("positions", [p.model_dump(mode="json") for p in self.positions.values()])
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Tick döngüsü hatası")

    async def _status_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(2.0)
                if self.bus.subscriber_count:
                    self.bus.publish("status", await self.status())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Durum döngüsü hatası")

    async def _equity_loop(self) -> None:
        last_store = 0
        last_retry = now_ms()
        while True:
            try:
                await asyncio.sleep(10.0 if self.env == "paper" else 30.0)
                if self.broker is None:
                    # Geçici ağ hatasından sonra bağlantıyı kendiliğinden yeniden dene
                    if self.broker_state == "error" and now_ms() - last_retry >= 60_000:
                        last_retry = now_ms()
                        async with self._exec_lock:
                            if self.broker is None:
                                await self._init_broker()
                    continue
                self.account = await self.broker.account()
                ts = now_ms()
                await self.risk.update_equity(self.env, self.account.equity, ts)
                if ts - last_store >= 60_000:
                    await self.db.add_equity(self.env, ts, self.account.equity, self.account.wallet, self.account.unrealized)
                    last_store = ts
                await self._evaluate_breakers()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Özsermaye güncellenemedi: %s", exc)

    async def _evaluate_breakers(self) -> None:
        extra: list[Breaker] = []
        if self._unknown_positions:
            extra.append(Breaker(
                code="unknown_position", label="Tanınmayan pozisyon", active=True,
                detail="Borsada botun açmadığı pozisyon var: " + ", ".join(sorted(self._unknown_positions)),
            ))
        if self.market.state == "down":
            extra.append(Breaker(code="market_down", label="Piyasa verisi yok", active=True, detail=self.market.state_message or "Bağlantı yok"))
        self._breakers = self.risk.breakers(self.env, self.store.runtime, self.recent_closed, extra)
        active = {b.code for b in self._breakers if b.active}
        for b in self._breakers:
            if b.active and b.code not in self._active_breakers and b.code != "paused":
                await self.events.log("warning", "breaker", f"Devre kesici: {b.label} — {b.detail}", toast=True, title="Devre kesici")
                self.notifier.notify(f"⛔ <b>Devre kesici</b>: {esc(b.label)}\n{esc(b.detail)}", kind="system")
        self._active_breakers = active

    async def _housekeeping_loop(self) -> None:
        n = 0
        while True:
            try:
                await asyncio.sleep(10.0)
                now = now_ms()
                for s in list(self.signals.values()):
                    if s.status == "ACTIVE":
                        s.price = self.market.price(s.symbol)
                        if now > s.expires_at:
                            s.status = "EXPIRED"
                            await self._save_signal(s)
                    elif now - s.created_at > 24 * 3600_000:
                        self.signals.pop(s.id, None)
                n += 1
                if n % 3 == 0:
                    await self._refresh_signal_plans()
                if n % 6 == 0:
                    await self._maybe_daily_report()
                if n % 360 == 0:
                    await self.db.prune_events()
                if not self._breakers:
                    await self._evaluate_breakers()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Bakım döngüsü hatası")

    async def _refresh_signal_plans(self) -> None:
        """Aktif fırsatların planını güncel fiyat ve portföy durumuyla yeniden hesaplar.

        Otomatik mod açıkken limit vb. nedeniyle bekletilmiş bir fırsat uygun hâle
        gelirse (ve fiyat hâlâ sinyal seviyesine yakınsa) otomatik olarak işlenir.
        """
        if self.broker is None:
            return
        for s in list(self.signals.values()):
            if s.status != "ACTIVE":
                continue
            price = self.market.price(s.symbol)
            if not price:
                continue
            sgn = side_sign(s.side)
            risk_unit = abs(s.entry - s.stop)
            drift_r = sgn * (price - s.entry) / risk_unit if risk_unit > 0 else 0.0
            if sgn * (price - s.stop) <= 0:
                plan = SizingPlan(ok=False, reason="Fiyat stop seviyesini geçti; fırsat geçersiz")
            else:
                try:
                    plan = await self._plan(s.symbol, s.side, price, s.stop, s.score, "auto")
                except Exception:
                    continue
                if plan.ok and drift_r > self.store.risk.max_entry_drift_r:
                    plan.ok = False
                    plan.reason = f"Fiyat sinyal seviyesinden {drift_r:.2f}R uzaklaştı; kovalanmıyor"
            old = s.plan
            changed = (
                old is None or old.ok != plan.ok or old.reason != plan.reason or old.leverage != plan.leverage
                or abs(old.notional - plan.notional) > 0.02 * max(old.notional, 1.0)
            )
            if changed:
                s.plan = plan
                self.bus.publish("signal", s.model_dump(mode="json"))
            if (
                plan.ok and self.store.runtime.auto_mode
                and s.status_note.startswith("Otomatik mod işlemedi")
                and self._auto_allowed(s)[0]
            ):
                try:
                    await self.execute_signal(s.id, source="auto")
                except EngineError as exc:
                    s.status_note = f"Otomatik işlenemedi: {exc}"
                    await self._save_signal(s)

    async def _reconcile_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(60.0)
                if self.broker is None or self.broker.kind == "paper":
                    continue
                await self.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Mutabakat hatası: %s", exc)

    async def reconcile(self) -> None:
        if self.broker is None:
            return
        bpos = await self.broker.positions()
        ours = {p.symbol: p for p in self.positions.values()}
        for sym, p in list(ours.items()):
            bp = bpos.get(sym)
            if bp is None:
                await self.events.log("warning", "reconcile", f"{sym}: borsada kapalı bulundu, kayıt kapatılıyor")
                await self._finalize(p, "Borsada kapalı bulundu (mutabakat)")
                continue
            if bp.liq_price > 0:
                p.liq_price = bp.liq_price
            if isinstance(self.broker, BinanceBroker):
                has = await self.broker.has_stop(sym)
                if has is False:
                    await self.events.log("error", "reconcile", f"{sym}: stopsuz pozisyon tespit edildi, stop yeniden yerleştiriliyor", toast=True, title="Stop eksik")
                    try:
                        await self.broker.replace_stop(sym, p.side, D(fmt(D(p.stop_price))))
                    except Exception as exc:
                        await self.events.log("error", "reconcile", f"{sym}: stop yerleştirilemedi ({exc}); pozisyon kapatılıyor")
                        async with self._exec_lock:
                            await self._close_internal(p.id, 1.0, "Stop yerleştirilemedi (güvenlik kapanışı)")
        unknown = {s for s in bpos if s not in ours}
        if unknown != self._unknown_positions:
            self._unknown_positions = unknown
            if unknown:
                await self.events.log("warning", "reconcile", "Botun tanımadığı pozisyon(lar): " + ", ".join(sorted(unknown)), toast=True, title="Tanınmayan pozisyon")

    # --- kontrol -----------------------------------------------------------
    async def set_auto(self, enabled: bool) -> None:
        await self.store.update("runtime", {"auto_mode": enabled})
        await self.events.log(
            "warning" if enabled and self.env == "live" else "info", "control",
            "Otomatik mod AÇILDI" if enabled else "Otomatik mod kapatıldı", toast=True, title="Otomatik mod",
        )
        self.notifier.notify("🤖 Otomatik mod <b>AÇIK</b>" if enabled else "✋ Otomatik mod kapalı", kind="system")

    async def set_paused(self, paused: bool, reason: str = "") -> None:
        await self.store.update("runtime", {"paused": paused, "paused_reason": reason if paused else ""})
        await self._evaluate_breakers()
        await self.events.log("warning" if paused else "info", "control", "Yeni işlemler durduruldu" if paused else "İşlemlere devam ediliyor", toast=True, title="Kontrol")

    async def dismiss_signal(self, signal_id: str) -> Signal:
        sig = self.signals.get(signal_id) or await self.db.get_signal(signal_id)
        if sig is None:
            raise EngineError("Fırsat bulunamadı")
        if sig.status == "ACTIVE":
            sig.status = "DISMISSED"
            sig.status_note = "Kullanıcı tarafından yoksayıldı"
            await self._save_signal(sig)
        return sig

    async def reset_paper(self, balance: float) -> None:
        if not isinstance(self.broker, PaperBroker):
            raise EngineError("Simülasyon sıfırlama sadece simülasyon ortamında yapılabilir")
        async with self._exec_lock:
            for p in list(self.positions.values()):
                p.status = "CLOSED"
                p.closed_at = now_ms()
                p.close_reason = "Simülasyon sıfırlandı"
                await self.db.save_position(p)
            self.positions.clear()
            self.recent_closed = []
            await self.broker.reset(balance)
            await self.db.clear_equity("paper")
            await self.risk.reset("paper", balance)
            self.account = await self.broker.account()
        await self.events.log("info", "paper", f"Simülasyon {balance:.2f} USDT bakiye ile sıfırlandı", toast=True, title="Simülasyon")

    # --- sorgular ----------------------------------------------------------
    async def position_detail(self, pos_id: str) -> Optional[dict]:
        pos = self.positions.get(pos_id) or await self.db.get_position(pos_id)
        if pos is None:
            return None
        projection = None
        if pos.status == "OPEN":
            buf = self.market.buffer(pos.symbol, pos.timeframe)
            mark = self.market.mark(pos.symbol) or pos.mark_price
            if buf is not None and mark:
                projection = build_projection(pos, buf.arrays(), pos.timeframe, mark)
        return {"position": pos.model_dump(mode="json"), "projection": projection}

    def _equity_view(self) -> Optional[dict]:
        acct = self.account
        if acct is None:
            return None
        unreal = sum(p.unrealized_pnl for p in self.positions.values())
        equity = acct.wallet + unreal if self.broker and self.broker.kind != "paper" else acct.equity
        st = self.risk.state(self.env)
        open_risk = sum(open_risk_usdt(p, self.market.mark(p.symbol)) for p in self.positions.values())
        margin_used = acct.margin_used
        day_pnl = equity - st.day_start_equity if st.day_start_equity > 0 else 0.0
        hwm = max(st.hwm, equity)
        return {
            "equity": equity,
            "wallet": acct.wallet,
            "unrealized": unreal if self.broker and self.broker.kind != "paper" else acct.unrealized,
            "available": acct.available,
            "margin_used": margin_used,
            "margin_used_pct": margin_used / equity * 100.0 if equity > 0 else 0.0,
            "open_risk": open_risk,
            "open_risk_pct": open_risk / equity * 100.0 if equity > 0 else 0.0,
            "day_pnl": day_pnl,
            "day_pnl_pct": day_pnl / st.day_start_equity * 100.0 if st.day_start_equity > 0 else 0.0,
            "hwm": hwm,
            "drawdown_pct": (hwm - equity) / hwm * 100.0 if hwm > 0 else 0.0,
        }

    def stats_summary(self, closed: Optional[list[Position]] = None) -> dict:
        items = [p for p in (closed if closed is not None else self.recent_closed) if p.close_reason != "Simülasyon sıfırlandı"]
        nets = [p.realized_pnl - p.fees + p.funding for p in items]
        rs = [p.r_final for p in items if p.r_final is not None]
        wins = [x for x in nets if x > 0]
        losses = [x for x in nets if x <= 0]
        gross_win = sum(wins)
        gross_loss = -sum(losses)
        return {
            "trades": len(items),
            "win_rate": (len(wins) / len(items)) if items else None,
            "expectancy_r": (sum(rs) / len(rs)) if rs else None,
            "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else (None if not wins else float("inf")),
            "total_pnl": sum(nets),
        }

    async def status(self) -> dict:
        if self.broker and self.broker.kind == "paper":
            try:
                self.account = await self.broker.account()
            except Exception:
                pass
        eq = self._equity_view()
        equity = eq["equity"] if eq else 0.0
        r = self.store.risk
        stats = self.stats_summary()
        if stats["profit_factor"] == float("inf"):
            stats["profit_factor"] = None
        return {
            "env": self.env,
            "data_source": self.market.source,
            "auto_mode": self.store.runtime.auto_mode,
            "paused": self.store.runtime.paused,
            "paused_reason": self.store.runtime.paused_reason,
            "connection": {
                "market": self.market.state,
                "user_stream": self.broker.user_stream_state if self.broker and self.broker.kind != "paper" else "n/a",
                "broker": self.broker_state,
                "message": self.broker_message or self.market.state_message,
            },
            "equity": eq,
            "regime": self.regime.model_dump(),
            "breakers": [b.model_dump() for b in self._breakers],
            "counts": {
                "open_positions": len(self.positions),
                "active_signals": sum(1 for s in self.signals.values() if s.status == "ACTIVE"),
                "universe": len(self.market.universe),
            },
            "scanner": self.scanner,
            "limits": {
                "max_positions": r.max_positions,
                "effective_max_positions": self.risk.effective_max_positions(equity) if equity else r.max_positions,
                "base_risk_pct": r.base_risk_pct,
            },
            "stats": stats,
            "server_time": now_ms(),
            "version": VERSION,
        }

    # --- kalıcılık yardımcıları ---------------------------------------------
    async def _save_signal(self, sig: Signal) -> None:
        self.signals[sig.id] = sig
        await self.db.save_signal(sig)
        self.bus.publish("signal", sig.model_dump(mode="json"))

    async def _save_position(self, pos: Position) -> None:
        await self.db.save_position(pos)
        self.bus.publish("position", pos.model_dump(mode="json"))

    # --- Telegram komutları -------------------------------------------------
    def _register_commands(self) -> None:
        async def durum(_: list[str]) -> str:
            st = await self.status()
            eq = st["equity"] or {}
            return (
                f"<b>Durum</b> · {st['env']}\n"
                f"Otomatik mod: {'AÇIK' if st['auto_mode'] else 'kapalı'}{' · DURDURULDU' if st['paused'] else ''}\n"
                f"Özsermaye: {eq.get('equity', 0):.2f} USDT · bugün {eq.get('day_pnl', 0):+.2f}\n"
                f"Açık pozisyon: {st['counts']['open_positions']} · aktif fırsat: {st['counts']['active_signals']}\n"
                f"Rejim: {esc(st['regime']['label'])}"
            )

        async def pozisyonlar(_: list[str]) -> str:
            if not self.positions:
                return "Açık pozisyon yok."
            lines = ["<b>Açık pozisyonlar</b>"]
            for p in self.positions.values():
                lines.append(f"{esc(p.symbol)} {SIDE_TR[p.side]} {p.leverage}x · K/Z {p.pnl_total:+.2f} ({p.r_now:+.2f}R)")
            return "\n".join(lines)

        async def auto_on(_: list[str]) -> str:
            await self.set_auto(True)
            return "🤖 Otomatik mod açıldı."

        async def auto_off(_: list[str]) -> str:
            await self.set_auto(False)
            return "✋ Otomatik mod kapatıldı."

        async def durdur(_: list[str]) -> str:
            await self.set_paused(True, "Telegram komutu")
            return "⏸ Yeni işlemler durduruldu. Mevcut pozisyonlar yönetilmeye devam ediyor."

        async def devam(_: list[str]) -> str:
            await self.set_paused(False)
            return "▶️ İşlemlere devam ediliyor."

        self.notifier.commands.update({
            "/durum": durum, "/pozisyonlar": pozisyonlar, "/otomatik_ac": auto_on,
            "/otomatik_kapat": auto_off, "/durdur": durdur, "/devam": devam,
        })
