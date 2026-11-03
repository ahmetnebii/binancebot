"""Backtest işlerini arka planda çalıştırır, ilerlemeyi yayınlar, sonuçları saklar ve kalibrasyonu uygular."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Optional

from ..market.universe import eligible
from ..models import SymbolInfo
from ..util import new_id, now_ms
from .data import BacktestCancelled, BinanceHistory, SyntheticHistory
from .report import build_result, short_summary
from .simulator import STEP, Backtester, BacktestConfig

if TYPE_CHECKING:  # pragma: no cover
    from ..api.context import AppContext

log = logging.getLogger(__name__)

WARMUP_MS = 12 * 86_400_000
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"]


class BacktestBusy(Exception):
    pass


class BacktestManager:
    def __init__(self, ctx: "AppContext"):
        self.ctx = ctx
        self._task: Optional[asyncio.Task] = None
        self._cancel = threading.Event()
        self._job: Optional[dict] = None
        self._last_pub = 0.0
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        for job in await self.ctx.db.bt_list(50):
            if job.get("status") in ("queued", "running"):
                job["status"] = "failed"
                job["error"] = "Sunucu yeniden başladığı için iş yarıda kaldı"
                job["finished_at"] = now_ms()
                await self.ctx.db.bt_save(job)

    async def stop(self) -> None:
        self._cancel.set()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    # --- yapılandırma ----------------------------------------------------
    def _resolve_symbols(self, top_n: int, explicit: Optional[list[str]]) -> list[str]:
        eng = self.ctx.engine
        market = eng.market if eng else None
        if explicit:
            syms = [s.strip().upper() for s in explicit if s.strip()]
            if market is not None and market.symbols:
                syms = [s for s in syms if s in market.symbols]
        elif market is not None and market.tickers:
            now = now_ms()
            cfg = self.ctx.store.scanner
            ranked = sorted(
                (t.quote_volume, s) for s, t in market.tickers.items()
                if s in market.symbols and eligible(market.symbols[s], t, cfg, now)[0]
            )
            syms = [s for _, s in reversed(ranked)][:top_n]
        else:
            syms = DEFAULT_SYMBOLS[:top_n]
        if "BTCUSDT" not in syms:
            syms = ["BTCUSDT"] + syms  # rejim tespiti için gerekli
        return syms

    def _resolve_config(self, body: dict[str, Any]) -> dict:
        days = int(body.get("days") or 90)
        if not 7 <= days <= 365:
            raise ValueError("Süre 7 ile 365 gün arasında olmalı")
        top_n = int(body.get("top_n") or 25)
        if not 5 <= top_n <= 80:
            raise ValueError("Sembol sayısı 5 ile 80 arasında olmalı")
        explicit = body.get("symbols") or None
        symbols = self._resolve_symbols(top_n, explicit)
        if len(symbols) < 2:
            raise ValueError("Test için en az 2 geçerli sembol gerekli")
        cur = self.ctx.store.strategies
        st = body.get("strategies") or {}
        strategies = {
            "s1": bool(st.get("s1", cur.s1_enabled)),
            "s2": bool(st.get("s2", cur.s2_enabled)),
            "s3": bool(st.get("s3", cur.s3_enabled)),
        }
        if not any(strategies.values()):
            raise ValueError("En az bir strateji seçilmeli")
        paper = self.ctx.store.paper
        initial = float(body.get("initial_equity") or 1000.0)
        if not 10 <= initial <= 100_000_000:
            raise ValueError("Başlangıç sermayesi 10 USDT ile 100M USDT arasında olmalı")
        cfg = {
            "days": days,
            "top_n": top_n,
            "symbols": symbols,
            "strategies": strategies,
            "initial_equity": initial,
            "slippage_bps": float(body.get("slippage_bps") if body.get("slippage_bps") is not None else max(paper.slippage_bps, 3.0)),
            "taker_fee": float(body.get("taker_fee") if body.get("taker_fee") is not None else paper.taker_fee),
            "maker_fee": float(body.get("maker_fee") if body.get("maker_fee") is not None else paper.maker_fee),
        }
        if not (0 <= cfg["slippage_bps"] <= 100 and 0 <= cfg["taker_fee"] <= 0.01 and 0 <= cfg["maker_fee"] <= 0.01):
            raise ValueError("Kayma veya komisyon değeri geçersiz")
        return cfg

    # --- iş yönetimi -----------------------------------------------------
    async def run(self, body: dict[str, Any]) -> dict:
        if self._task is not None and not self._task.done():
            raise BacktestBusy("Zaten çalışan bir backtest var; bitmesini bekleyin veya iptal edin")
        cfg = self._resolve_config(body)
        job = {
            "id": new_id("bt"),
            "created_at": now_ms(),
            "finished_at": None,
            "status": "queued",
            "progress": {"phase": "data", "pct": 0.0, "message": "Sırada"},
            "config": cfg,
            "error": None,
            "summary": None,
        }
        await self.ctx.db.bt_save(job)
        self._cancel.clear()
        self._job = job
        self._publish(job, force=True)
        self._task = asyncio.create_task(self._run(job), name=f"backtest-{job['id']}")
        return job

    def _publish(self, job: dict, force: bool = False) -> None:
        t = time.monotonic()
        if not force and t - self._last_pub < 0.4:
            return
        self._last_pub = t
        self.ctx.bus.publish("backtest", {k: v for k, v in job.items() if k != "result"})

    def _set_progress(self, job: dict, phase: str, pct: float, message: str) -> None:
        job["progress"] = {"phase": phase, "pct": round(max(0.0, min(100.0, pct)), 1), "message": message}
        self._publish(job)

    def _thread_progress(self, job: dict, phase: str, lo: float, hi: float):
        def cb(frac: float, message: str) -> None:
            if self._loop is None:
                return
            self._loop.call_soon_threadsafe(self._set_progress, job, phase, lo + (hi - lo) * frac, message)
        return cb

    async def _run(self, job: dict) -> None:
        cfg = job["config"]
        store = self.ctx.store
        job["status"] = "running"
        await self.ctx.db.bt_save(job)
        self._publish(job, force=True)
        try:
            end = now_ms()
            end -= end % STEP
            start = end - cfg["days"] * 86_400_000
            if self.ctx.synthetic:
                provider = SyntheticHistory()
            else:
                provider = BinanceHistory(store.connection.live_rest, self.ctx.data_dir / "history")
            data = await provider.load(
                cfg["symbols"], start - WARMUP_MS, end,
                progress=lambda f, m: self._set_progress(job, "data", f * 40.0, m),
                cancelled=self._cancel.is_set,
            )
            if "BTCUSDT" not in data.bars:
                raise RuntimeError("BTCUSDT verisi alınamadı; rejim tespiti yapılamıyor")
            infos: dict[str, SymbolInfo] = {}
            eng = self.ctx.engine
            if eng is not None:
                infos = {s: eng.market.symbols[s] for s in cfg["symbols"] if s in eng.market.symbols}
            strat = store.strategies.model_copy(update={
                "s1_enabled": cfg["strategies"]["s1"], "s2_enabled": cfg["strategies"]["s2"], "s3_enabled": cfg["strategies"]["s3"],
            })
            bt_cfg = BacktestConfig(
                symbols=cfg["symbols"], start_ms=start, end_ms=end, initial_equity=cfg["initial_equity"],
                s1=cfg["strategies"]["s1"], s2=cfg["strategies"]["s2"], s3=cfg["strategies"]["s3"],
                slippage_bps=cfg["slippage_bps"], taker_fee=cfg["taker_fee"], maker_fee=cfg["maker_fee"],
                risk=store.risk.model_copy(), strategies=strat, scanner=store.scanner.model_copy(),
            )
            sim = Backtester(bt_cfg, data, infos, self._thread_progress(job, "simulate", 40.0, 95.0), self._cancel.is_set)
            raw = await asyncio.to_thread(sim.run)
            self._set_progress(job, "analyze", 96.0, "Metrikler, Monte Carlo ve dönemsel analiz hesaplanıyor")
            warnings: list[str] = []
            if data.source == "synthetic":
                warnings.append("Sentetik geliştirme verisi kullanıldı: sonuçlar gerçek piyasa performansını yansıtmaz.")
            else:
                warnings.append(
                    "Sembol evreni bugünkü hacim sıralamasından seçildi; o dönemde listede olmayan veya sonradan "
                    "delist edilen coinler hariç (hayatta kalma yanılgısı içerebilir)."
                )
            if data.missing:
                warnings.append(f"Yetersiz veri nedeniyle atlanan semboller: {', '.join(data.missing[:15])}")
            result = await asyncio.to_thread(
                build_result, raw, initial_equity=cfg["initial_equity"], start_ms=start, end_ms=end,
                source=data.source, extra_warnings=warnings,
            )
            job["status"] = "done"
            job["finished_at"] = now_ms()
            job["summary"] = short_summary(result)
            job["progress"] = {"phase": "done", "pct": 100.0, "message": "Tamamlandı"}
            await self.ctx.db.bt_save(job, result)
            s = result["summary"]
            pf = f"{s['profit_factor']:.2f}" if s["profit_factor"] is not None else "—"
            ex = f"{s['expectancy_r']:+.2f}R" if s["expectancy_r"] is not None else "—"
            await self.ctx.events.log(
                "success", "backtest",
                f"Backtest tamamlandı: {s['trades']} işlem, PF {pf}, beklenti {ex}, getiri %{s['total_return_pct']:+.1f}, "
                f"maks. düşüş %{s['max_drawdown_pct']:.1f}",
                toast=True, title="Backtest tamamlandı",
            )
        except (BacktestCancelled, asyncio.CancelledError):
            job["status"] = "cancelled"
            job["finished_at"] = now_ms()
            job["progress"] = {**job["progress"], "message": "İptal edildi"}
            await self.ctx.db.bt_save(job)
        except Exception as exc:
            log.exception("Backtest hatası")
            job["status"] = "failed"
            job["finished_at"] = now_ms()
            job["error"] = str(exc)
            await self.ctx.db.bt_save(job)
            await self.ctx.events.log("error", "backtest", f"Backtest başarısız: {exc}", toast=True, title="Backtest hatası")
        finally:
            self._publish(job, force=True)

    async def cancel(self, job_id: str) -> Optional[dict]:
        if self._job and self._job["id"] == job_id and self._task and not self._task.done():
            self._cancel.set()
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=10)
            except asyncio.TimeoutError:
                self._task.cancel()
        return await self.ctx.db.bt_get(job_id, with_result=False)

    async def get(self, job_id: str) -> Optional[dict]:
        if self._job and self._job["id"] == job_id and self._job["status"] in ("queued", "running"):
            return {k: v for k, v in self._job.items()}
        return await self.ctx.db.bt_get(job_id)

    async def list(self) -> list[dict]:
        jobs = await self.ctx.db.bt_list(20)
        if self._job and self._job["status"] in ("queued", "running"):
            jobs = [self._job if j["id"] == self._job["id"] else j for j in jobs]
        return jobs

    async def delete(self, job_id: str) -> None:
        if self._job and self._job["id"] == job_id and self._job["status"] in ("queued", "running"):
            raise BacktestBusy("Çalışan iş silinemez; önce iptal edin")
        await self.ctx.db.bt_delete(job_id)

    # --- kalibrasyon -------------------------------------------------------
    @staticmethod
    def _empty_calibration() -> dict:
        return {"source_job": None, "applied_at": None, "strategies": {}}

    async def get_calibration(self) -> dict:
        cal = await self.ctx.db.kv_get("calibration")
        return cal if isinstance(cal, dict) else self._empty_calibration()

    async def apply_calibration(self, job_id: str) -> dict:
        job = await self.ctx.db.bt_get(job_id)
        if job is None or job.get("status") != "done" or "result" not in job:
            raise ValueError("Tamamlanmış bir backtest bulunamadı")
        cal = job["result"].get("calibration") or {}
        if not cal:
            raise ValueError("Kalibrasyon için hiçbir stratejide yeterli işlem (en az 10) yok")
        if job["result"].get("data_source") == "synthetic" and not self.ctx.synthetic:
            raise ValueError("Sentetik veriyle yapılmış test gerçek sisteme uygulanamaz")
        doc = {"source_job": job_id, "applied_at": now_ms(), "strategies": cal}
        await self.ctx.db.kv_set("calibration", doc, now_ms())
        if self.ctx.engine:
            self.ctx.engine.set_calibration(cal)
        names = ", ".join(f"{k} (n={v['n']}, kazanma %{v['win_rate'] * 100:.0f}, beklenti {v['expectancy_r']:+.2f}R)" for k, v in cal.items())
        await self.ctx.events.log("info", "backtest", f"Kalibrasyon uygulandı: {names}", toast=True, title="Kalibrasyon")
        return doc

    async def reset_calibration(self) -> dict:
        await self.ctx.db.kv_delete("calibration")
        if self.ctx.engine:
            self.ctx.engine.set_calibration({})
        await self.ctx.events.log("info", "backtest", "Kalibrasyon kaldırıldı; varsayılan istatistiklere dönüldü")
        return self._empty_calibration()
