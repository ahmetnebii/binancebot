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
from .lab import BUDGETS as LAB_BUDGETS, run_lab
from .optimizer import BUDGETS, describe, optimize, params_from_settings
from .simulator import STEP, Backtester, BacktestConfig

if TYPE_CHECKING:  # pragma: no cover
    from ..api.context import AppContext

log = logging.getLogger(__name__)

WARMUP_MS = 12 * 86_400_000
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"]



def _dedupe(board: list[dict]) -> list[dict]:
    """Aynı sonucu veren (etkisiz parametre farkı) adayları tekilleştirir."""
    seen: set = set()
    out = []
    for b in board:
        key = tuple((b[k]["trades"], b[k]["expectancy_r"], b[k]["return_pct"]) for k in ("is", "oos"))
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out

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
            "s4": bool(st.get("s4", cur.s4_enabled)),
            "s5": bool(st.get("s5", cur.s5_enabled)),
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
                "s4_enabled": cfg["strategies"].get("s4", False),
                "s5_enabled": cfg["strategies"].get("s5", False),
            })
            bt_cfg = BacktestConfig(
                symbols=cfg["symbols"], start_ms=start, end_ms=end, initial_equity=cfg["initial_equity"],
                s1=cfg["strategies"]["s1"], s2=cfg["strategies"]["s2"], s3=cfg["strategies"]["s3"],
                s4=cfg["strategies"].get("s4", False), s5=cfg["strategies"].get("s5", False),
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

    async def list(self, kind: str = "backtest") -> list[dict]:
        jobs = [j for j in await self.ctx.db.bt_list(60) if j.get("kind", "backtest") == kind][:20]
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


    # --- otomatik optimizasyon -------------------------------------------------
    VERDICT_TEXT = {
        "recommended": "Görülmemiş doğrulama döneminde de kâr eden, kapıları geçen bir ayar bulundu. Uygulanması önerilir.",
        "weak": "Doğrulama döneminde hafif kârlı bir ayar bulundu ama güçlü değil. Uygularsan önce Simülasyon'da izle.",
        "none": "Hem eğitim hem doğrulama döneminde anlamlı kâr eden bir ayar bulunamadı (sıfıra yakın sonuçlar gürültü sayılır). Bu piyasa koşullarında stratejilerin avantajı yok; gerçek parayla otomatik işlem önerilmez.",
    }

    async def run_optimize(self, body: dict[str, Any]) -> dict:
        if self._task is not None and not self._task.done():
            raise BacktestBusy("Zaten çalışan bir test veya optimizasyon var; bitmesini bekleyin veya iptal edin")
        days = int(body.get("days") or 180)
        if not 60 <= days <= 365:
            raise ValueError("Optimizasyon için süre 60 ile 365 gün arasında olmalı")
        top_n = int(body.get("top_n") or 30)
        if not 5 <= top_n <= 80:
            raise ValueError("Sembol sayısı 5 ile 80 arasında olmalı")
        budget = str(body.get("budget") or "normal")
        if budget not in BUDGETS:
            raise ValueError("Derinlik hizli, normal veya derin olmalı")
        cfg = self._resolve_config({"days": days, "top_n": top_n, "strategies": {"s1": True}})
        cfg["budget"] = budget
        cfg["candidates"] = BUDGETS[budget]
        job = {
            "id": new_id("opt"), "kind": "optimize", "created_at": now_ms(), "finished_at": None,
            "status": "queued", "progress": {"phase": "data", "pct": 0.0, "message": "Sırada"},
            "config": cfg, "error": None, "summary": None,
        }
        await self.ctx.db.bt_save(job)
        self._cancel.clear()
        self._job = job
        self._publish(job, force=True)
        self._task = asyncio.create_task(self._run_optimize(job), name=f"optimize-{job['id']}")
        return job

    async def _run_optimize(self, job: dict) -> None:
        cfg = job["config"]
        store = self.ctx.store
        job["status"] = "running"
        await self.ctx.db.bt_save(job)
        self._publish(job, force=True)
        try:
            end = now_ms()
            end -= end % STEP
            start = end - cfg["days"] * 86_400_000
            provider = SyntheticHistory() if self.ctx.synthetic else BinanceHistory(store.connection.live_rest, self.ctx.data_dir / "history")
            data = await provider.load(
                cfg["symbols"], start - WARMUP_MS, end,
                progress=lambda f, m: self._set_progress(job, "data", f * 15.0, m),
                cancelled=self._cancel.is_set,
            )
            if "BTCUSDT" not in data.bars:
                raise RuntimeError("BTCUSDT verisi alınamadı; rejim tespiti yapılamıyor")
            eng = self.ctx.engine
            infos = {s: eng.market.symbols[s] for s in cfg["symbols"] if eng and s in eng.market.symbols}
            base = {
                "symbols": cfg["symbols"], "initial_equity": cfg["initial_equity"], "slippage_bps": cfg["slippage_bps"],
                "taker_fee": cfg["taker_fee"], "maker_fee": cfg["maker_fee"],
                "strategies": store.strategies.model_dump(), "risk": store.risk.model_dump(), "scanner": store.scanner.model_dump(),
            }
            baseline = params_from_settings(store.strategies, store.risk)
            loop = asyncio.get_running_loop()

            def prog(phase: str, pct: float, msg: str) -> None:
                loop.call_soon_threadsafe(self._set_progress, job, phase, 15.0 + pct * 0.85, msg)

            out = await asyncio.to_thread(optimize, data, infos, base, baseline, start, end, cfg["budget"], prog, self._cancel.is_set)

            def row(b: Optional[dict]) -> Optional[dict]:
                if b is None:
                    return None
                return {**describe(b["params"]), "params": b["params"], "is": b["is"], "oos": b["oos"]}

            full_result = None
            if out["full"] is not None:
                warnings = ["Sentetik geliştirme verisi kullanıldı."] if data.source == "synthetic" else []
                full_result = await asyncio.to_thread(
                    build_result, out["full"]["raw"], initial_equity=cfg["initial_equity"], start_ms=start, end_ms=end,
                    source=data.source, extra_warnings=warnings,
                )
            warnings = [
                "Ayarlar eğitim döneminde seçildi, yalnızca hiç görülmemiş doğrulama döneminin sonucuna göre önerildi.",
                "Geçmişte iyi çalışan ayar gelecekte de çalışacak diye garanti yoktur; uygulamadan sonra Simülasyon'da izleyin.",
            ]
            if data.source == "synthetic":
                warnings.insert(0, "Sentetik geliştirme verisi kullanıldı: sonuçlar gerçek piyasayı yansıtmaz.")
            result = {
                "verdict": out["verdict"],
                "verdict_text": self.VERDICT_TEXT[out["verdict"]],
                "period": {"start": start, "split": out["split"], "end": end},
                "candidates_tested": out["candidates_tested"],
                "min_trades": out["min_trades"],
                "best": row(out["best"]),
                "baseline": row(out["baseline"]),
                "leaderboard": [{"rank": i + 1, **row(b)} for i, b in enumerate(_dedupe(out["board"])[:10])],
                "full": full_result,
                "calibration": (full_result or {}).get("calibration", {}),
                "data_source": data.source,
                "warnings": warnings,
            }
            b = result["best"]
            job["status"] = "done"
            job["finished_at"] = now_ms()
            job["summary"] = {
                "verdict": out["verdict"], "label": b["label"] if b else None,
                "oos_expectancy_r": b["oos"]["expectancy_r"] if b else None,
                "oos_profit_factor": b["oos"]["profit_factor"] if b else None,
                "oos_return_pct": b["oos"]["return_pct"] if b else None,
            }
            job["progress"] = {"phase": "done", "pct": 100.0, "message": "Tamamlandı"}
            await self.ctx.db.bt_save(job, result)
            await self.ctx.events.log(
                "success" if out["verdict"] != "none" else "warning", "optimize",
                f"Optimizasyon tamamlandı: {result['verdict_text']}", toast=True, title="Optimizasyon",
            )
        except (BacktestCancelled, asyncio.CancelledError):
            job["status"] = "cancelled"
            job["finished_at"] = now_ms()
            await self.ctx.db.bt_save(job)
        except Exception as exc:
            log.exception("Optimizasyon hatası")
            job["status"] = "failed"
            job["finished_at"] = now_ms()
            job["error"] = str(exc)
            await self.ctx.db.bt_save(job)
            await self.ctx.events.log("error", "optimize", f"Optimizasyon başarısız: {exc}", toast=True, title="Optimizasyon hatası")
        finally:
            self._publish(job, force=True)

    async def apply_optimization(self, job_id: str) -> dict:
        job = await self.ctx.db.bt_get(job_id)
        if job is None or job.get("kind") != "optimize" or job.get("status") != "done" or "result" not in job:
            raise ValueError("Tamamlanmış bir optimizasyon bulunamadı")
        res = job["result"]
        if res.get("verdict") == "none" or not res.get("best"):
            raise ValueError("Bu optimizasyon uygulanabilir bir ayar bulamadı")
        if res.get("data_source") == "synthetic" and not self.ctx.synthetic:
            raise ValueError("Sentetik veriyle yapılmış optimizasyon gerçek sisteme uygulanamaz")
        best = res["best"]["params"]
        store = self.ctx.store
        await store.update("strategies", best["strategies"])
        await store.update("risk", {"min_ev_r": best["min_ev_r"]})
        cal = res.get("calibration") or {}
        if cal:
            doc = {"source_job": job_id, "applied_at": now_ms(), "strategies": cal}
            await self.ctx.db.kv_set("calibration", doc, now_ms())
            if self.ctx.engine:
                self.ctx.engine.set_calibration(cal)
        job["applied_at"] = now_ms()
        await self.ctx.db.bt_save(job)
        self._publish(job, force=True)
        await self.ctx.events.log(
            "success", "optimize", f"Optimize ayarlar uygulandı: {res['best']['label']}; canlı tarama bu ayarlarla devam ediyor",
            toast=True, title="Ayarlar uygulandı",
        )
        return {"ok": True, "applied": describe(best), "calibrated": bool(cal)}

    # --- Strateji Laboratuvarı ------------------------------------------------
    LAB_VERDICT_TEXT = {
        "recommended": "Seçilen kural, aramada HİÇ kullanılmamış final sınavı döneminde de kâr etti ve şans çıtasını geçti. Uygulanması önerilir.",
        "weak": "Final sınavında hafif kârlı bir kural bulundu ama kanıt güçlü değil (kâr küçük ya da şans çıtasının altında). Uygularsan Simülasyon'da izle.",
        "none": "Binlerce kural denendi; hiçbiri eğitim, doğrulama ve final sınavının hepsinde yeterli kanıtla kâr etmedi.",
    }

    async def run_lab_job(self, body: dict[str, Any]) -> dict:
        if self._task is not None and not self._task.done():
            raise BacktestBusy("Zaten çalışan bir test, optimizasyon veya laboratuvar araması var; bitmesini bekleyin veya iptal edin")
        days = int(body.get("days") or 365)
        if not 90 <= days <= 1095:
            raise ValueError("Laboratuvar için süre 90 ile 1095 gün (3 yıl) arasında olmalı")
        top_n = int(body.get("top_n") or 30)
        if not 10 <= top_n <= 60:
            raise ValueError("Sembol sayısı 10 ile 60 arasında olmalı")
        budget = str(body.get("budget") or "normal")
        if budget not in LAB_BUDGETS:
            raise ValueError("Derinlik hizli, normal, derin veya maksimum olmalı")
        grouped = bool(body.get("grouped", False))
        if grouped and top_n < 20:
            raise ValueError("Coin grubu bazlı arama için en az 20 sembol gerekli")
        cfg = self._resolve_config({"days": min(days, 365), "top_n": top_n, "strategies": {"s1": True}})
        cfg["days"] = days
        cfg["budget"] = budget
        cfg["candidates"] = LAB_BUDGETS[budget]
        cfg["grouped"] = grouped
        job = {
            "id": new_id("lab"), "kind": "lab", "created_at": now_ms(), "finished_at": None,
            "status": "queued", "progress": {"phase": "data", "pct": 0.0, "message": "Sırada"},
            "config": cfg, "error": None, "summary": None,
        }
        await self.ctx.db.bt_save(job)
        self._cancel.clear()
        self._job = job
        self._publish(job, force=True)
        self._task = asyncio.create_task(self._run_lab(job), name=f"lab-{job['id']}")
        return job

    async def _run_lab(self, job: dict) -> None:
        cfg = job["config"]
        store = self.ctx.store
        job["status"] = "running"
        await self.ctx.db.bt_save(job)
        self._publish(job, force=True)
        try:
            end = now_ms()
            end -= end % STEP
            start = end - cfg["days"] * 86_400_000
            provider = SyntheticHistory() if self.ctx.synthetic else BinanceHistory(store.connection.live_rest, self.ctx.data_dir / "history")
            data = await provider.load(
                cfg["symbols"], start - WARMUP_MS, end,
                progress=lambda f, m: self._set_progress(job, "data", f * 10.0, m),
                cancelled=self._cancel.is_set,
            )
            if "BTCUSDT" not in data.bars:
                raise RuntimeError("BTCUSDT verisi alınamadı; rejim tespiti yapılamıyor")
            eng = self.ctx.engine
            infos = {s: eng.market.symbols[s] for s in cfg["symbols"] if eng and s in eng.market.symbols}
            base = {
                "symbols": cfg["symbols"], "initial_equity": cfg["initial_equity"], "slippage_bps": cfg["slippage_bps"],
                "taker_fee": cfg["taker_fee"], "maker_fee": cfg["maker_fee"],
                "strategies": store.strategies.model_dump(), "risk": store.risk.model_dump(), "scanner": store.scanner.model_dump(),
                "cache_mb": 500 if cfg["days"] <= 400 else 350,  # uzun geçmişte veri büyür, önbellek küçülür
            }
            loop = asyncio.get_running_loop()

            def prog(phase: str, pct: float, msg: str) -> None:
                loop.call_soon_threadsafe(self._set_progress, job, phase, 10.0 + pct * 0.9, msg)

            out = await asyncio.to_thread(
                run_lab, data, infos, base, start, end, cfg["budget"], prog, self._cancel.is_set, 20261103, cfg.get("grouped", False),
            )
            groups = []
            for g in out["groups"]:
                full_result = None
                if g["full"] is not None:
                    warnings = ["Sentetik geliştirme verisi kullanıldı."] if data.source == "synthetic" else []
                    full_result = await asyncio.to_thread(
                        build_result, g["full"]["raw"], initial_equity=cfg["initial_equity"], start_ms=start, end_ms=end,
                        source=data.source, extra_warnings=warnings,
                    )
                groups.append({
                    "key": g["key"], "name": g["name"], "symbols": g["symbols"], "vol_min": g["vol_min"], "vol_max": g["vol_max"],
                    "verdict": g["verdict"], "verdict_text": self.LAB_VERDICT_TEXT[g["verdict"]], "forward_ok": g["forward_ok"],
                    "tested": g["tested"], "luck_bar": g["luck_bar"], "min_trades": g["min_trades"],
                    "shortlist": g["shortlist"], "passed_oos": g["passed_oos"], "best": g["best"],
                    "finalists": g["finalists"], "leaderboard": g["leaderboard"], "full": full_result,
                    "calibration": (full_result or {}).get("calibration", {}),
                })
            tested = sum(g["tested"] for g in groups)
            warnings = [
                "Dönem üçe bölündü: eğitim %60 (arama), doğrulama %20 (eleme), final sınavı %20 (aramada hiç görülmedi).",
                "Şans çıtası: N denemede saf şansla görülebilecek en yüksek t-değeri ≈ √(2·ln N); her grup için ayrı hesaplanır.",
                "Geçmişte çalışan kural gelecekte de çalışacak diye garanti yoktur; uygulamadan sonra Simülasyon'da izleyin.",
            ]
            if cfg.get("grouped"):
                warnings.insert(1, "Coinler yalnızca eğitim dönemindeki hacim ve oynaklıklarına göre gruplandı; her gruba ayrı kural arandı.")
            if data.source == "synthetic":
                warnings.insert(0, "Sentetik geliştirme verisi kullanıldı: sonuçlar gerçek piyasayı yansıtmaz.")
            n_ok = sum(1 for g in groups if g["verdict"] != "none")
            n_fw = sum(1 for g in groups if g["forward_ok"])
            if out["verdict"] != "none":
                vtext = self.LAB_VERDICT_TEXT[out["verdict"]] if len(groups) == 1 else f"{len(groups)} grubun {n_ok} tanesinde doğrulanmış kural bulundu."
            elif n_fw:
                vtext = (f"Kanıt henüz yeterli değil, ama {n_fw} grupta dört aşamanın hepsinde kârlı aday var. "
                         "Gerçek parayla değil, Simülasyon'da canlı denemeye değer.")
            else:
                vtext = self.LAB_VERDICT_TEXT["none"] + " Bu veride gerçek bir avantaj bulunamadı; gerçek parayla otomatik işlem önerilmez."
            result = {
                "verdict": out["verdict"], "verdict_text": vtext, "grouped": bool(cfg.get("grouped")),
                "period": {"start": start, "split1": out["split1"], "split2": out["split2"], "end": end},
                "tested": tested, "workers": out["workers"], "groups": groups,
                "data_source": data.source, "warnings": warnings,
            }
            job["status"] = "done"
            job["finished_at"] = now_ms()
            job["summary"] = {
                "verdict": out["verdict"], "tested": tested, "groups": len(groups), "ok_groups": n_ok, "forward_groups": n_fw,
                "label": next((g["best"]["label"] for g in groups if g["best"]), None),
            }
            job["progress"] = {"phase": "done", "pct": 100.0, "message": "Tamamlandı"}
            await self.ctx.db.bt_save(job, result)
            await self.ctx.events.log(
                "success" if out["verdict"] != "none" else "warning", "lab",
                f"Strateji Laboratuvarı tamamlandı: {vtext}", toast=True, title="Strateji Laboratuvarı",
            )
        except (BacktestCancelled, asyncio.CancelledError):
            job["status"] = "cancelled"
            job["finished_at"] = now_ms()
            await self.ctx.db.bt_save(job)
        except Exception as exc:
            log.exception("Laboratuvar hatası")
            job["status"] = "failed"
            job["finished_at"] = now_ms()
            job["error"] = str(exc)
            await self.ctx.db.bt_save(job)
            await self.ctx.events.log("error", "lab", f"Laboratuvar araması başarısız: {exc}", toast=True, title="Laboratuvar hatası")
        finally:
            self._publish(job, force=True)

    async def apply_lab(self, job_id: str, mode: str = "live") -> dict:
        """mode="live": yalnızca doğrulanmış (önerilen/zayıf) grupları uygular.
        mode="paper": dört aşamada da kârlı adayları DENEME kuralı olarak uygular (yalnızca Simülasyon'da işlenir)."""
        job = await self.ctx.db.bt_get(job_id)
        if job is None or job.get("kind") != "lab" or job.get("status") != "done" or "result" not in job:
            raise ValueError("Tamamlanmış bir laboratuvar araması bulunamadı")
        res = job["result"]
        if res.get("data_source") == "synthetic" and not self.ctx.synthetic:
            raise ValueError("Sentetik veriyle yapılmış arama gerçek sisteme uygulanamaz")
        groups = res.get("groups")
        if groups is None:  # eski tek-kurallı sonuç biçimi
            groups = [{"key": "all", "symbols": [], "vol_min": None, "vol_max": None, "verdict": res.get("verdict"),
                       "best": res.get("best"), "forward_ok": False, "calibration": res.get("calibration") or {}}]
        if mode == "paper":
            chosen = [g for g in groups if g.get("best") and (g.get("forward_ok") or g.get("verdict") != "none")]
            if not chosen:
                raise ValueError("Simülasyonda denenecek, dört aşamada da kârlı bir aday yok")
        else:
            chosen = [g for g in groups if g.get("best") and g.get("verdict") != "none"]
            if not chosen:
                raise ValueError("Bu arama doğrulanmış bir kural bulamadı; istersen 'Simülasyonda canlı dene' ile deneyebilirsin")
        paper_only = mode == "paper"
        patch: dict[str, Any] = {
            "lab_enabled": True, "s1_enabled": False, "s2_enabled": False,
            "s3_enabled": False, "s4_enabled": False, "s5_enabled": False,
        }
        if len(chosen) == 1 and chosen[0]["key"] == "all":
            patch.update(lab_rule=chosen[0]["best"]["rule"], lab_groups=None, lab_paper_only=paper_only)
        else:
            patch.update(lab_rule=None, lab_paper_only=False, lab_groups=[{
                "key": g["key"], "symbols": g["symbols"], "vol_min": g.get("vol_min"), "vol_max": g.get("vol_max"),
                "rule": g["best"]["rule"], "label": g["best"]["label"],
                "paper_only": paper_only or g.get("verdict") == "none",
            } for g in chosen])
        store = self.ctx.store
        await store.update("strategies", patch)
        cal: dict = {}
        if len(chosen) == 1:
            cal = chosen[0].get("calibration") or {}
        if cal:
            doc = {"source_job": job_id, "applied_at": now_ms(), "strategies": cal}
            await self.ctx.db.kv_set("calibration", doc, now_ms())
            if self.ctx.engine:
                self.ctx.engine.set_calibration(cal)
        job["applied_at"] = now_ms()
        job["applied_mode"] = mode
        await self.ctx.db.bt_save(job)
        self._publish(job, force=True)
        labels = ", ".join(g["best"]["label"] for g in chosen)
        what = "Simülasyon'da canlı deneme başladı" if paper_only else "canlı tarama artık bu kurallarla fırsat arıyor"
        await self.ctx.events.log("success", "lab", f"Laboratuvar kuralları uygulandı ({labels}); {what}", toast=True, title="Kural uygulandı")
        return {"ok": True, "mode": mode, "groups": [g["key"] for g in chosen], "calibrated": bool(cal)}
