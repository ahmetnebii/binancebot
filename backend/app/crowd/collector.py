"""Saatlik kalabalık verisi toplayıcı + canlı funding radarı + sanal funding portföyü.

Binance ücretsiz uç noktaları:
- /futures/data/openInterestHist           açık pozisyon (adet ve USDT)
- /futures/data/globalLongShortAccountRatio tüm hesapların long/short oranı
- /futures/data/topLongShortPositionRatio   büyük hesapların long/short pozisyon oranı
- /futures/data/takerlongshortRatio         agresif alım/satım hacim oranı
- /fapi/v1/fundingRate                       funding geçmişi
İlk dördü yalnızca SON ~30 GÜN için geçmiş verir; bu yüzden toplayıcı her saat veriyi kendi veritabanına
yazar ve zamanla uzun bir geçmiş biriktirir. İlk çalıştırmada son ~20 gün (500 saat) bir kez doldurulur.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
from typing import TYPE_CHECKING, Any, Optional

import aiohttp
import numpy as np

from ..backtest.carry import PER_YEAR, WINDOW_MS, portfolio_step
from ..util import now_ms

if TYPE_CHECKING:  # pragma: no cover
    from ..api.context import AppContext

log = logging.getLogger(__name__)
HOUR = 3_600_000
DAY = 86_400_000
TOP_SYMBOLS = 30        # kalabalık verisi (5 uç nokta) toplanan coin sayısı
FUNDING_SYMBOLS = 600   # funding radarı için izlenen coin sayısı (tek uç nokta, ucuz)
DEFAULT_CARRY = {"k": 21, "enter_apr": 10.0, "exit_apr": 0.0, "top_n": 5, "leverage": 3.0,
                 "spot_fee": 0.001, "perp_fee": 0.0005, "slippage": 0.0002}


class CrowdCollector:
    def __init__(self, ctx: "AppContext"):
        self.ctx = ctx
        self._task: Optional[asyncio.Task] = None
        self._last_hour = -1
        self.state = "bekliyor"
        self.last_run: Optional[int] = None
        self.last_error: Optional[str] = None
        self._rng = random.Random(7)

    # --- yaşam döngüsü ---------------------------------------------------------
    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="crowd-collector")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _loop(self) -> None:
        await asyncio.sleep(15)  # piyasa verisi açılsın
        while True:
            try:
                hour = now_ms() // HOUR
                if hour != self._last_hour:
                    await self.collect()
                    self._last_hour = hour
                    await self.carry_paper_update()
                    await self.ml_paper_update()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # toplayıcı botu asla durdurmamalı
                log.exception("Kalabalık verisi toplanamadı")
                self.last_error = str(exc)
                self.state = "hata"
            await asyncio.sleep(60)

    def symbols(self, limit: int = TOP_SYMBOLS) -> list[str]:
        eng = self.ctx.engine
        if eng is None:
            return []
        m = eng.market
        ranked = sorted(((t.quote_volume, s) for s, t in m.tickers.items() if s in m.universe), reverse=True)
        syms = [s for _, s in ranked][:limit]
        if not syms:
            syms = list(m.universe)[:limit]
        return syms

    def funding_symbols(self) -> list[str]:
        """Funding için likidite filtresinden geçen tüm vadeli coinler (en fazla FUNDING_SYMBOLS)."""
        eng = self.ctx.engine
        if eng is None:
            return []
        m = eng.market
        ranked = sorted(((t.quote_volume, s) for s, t in m.tickers.items() if s in m.symbols and s.endswith("USDT")), reverse=True)
        return [s for v, s in ranked if v >= 1_000_000][:FUNDING_SYMBOLS] or self.symbols()

    # --- toplama ---------------------------------------------------------------
    async def collect(self) -> None:
        syms = self.symbols()
        if not syms:
            return
        self.state = "topluyor"
        if self.ctx.synthetic:
            await self._collect_synthetic(syms)
        else:
            await self._collect_binance(syms)
        self.last_run = now_ms()
        self.state = "hazır"
        self.last_error = None

    async def _collect_binance(self, syms: list[str]) -> None:
        base = self.ctx.store.connection.live_rest.rstrip("/")
        db = self.ctx.db
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:

            async def get(path: str, params: dict) -> list:
                return await self._fetch(s, base, path, params)

            now = now_ms()
            for sym in syms:
                last = await db.crowd_last_t(sym)
                hours = 500 if last is None else int(min(500, max(3, (now - last) // HOUR + 2)))
                rows: dict[int, dict[str, Any]] = {}
                for r in await get("/futures/data/openInterestHist", {"symbol": sym, "period": "1h", "limit": hours}):
                    rows.setdefault(int(r["timestamp"]), {}).update(oi=float(r["sumOpenInterest"]), oi_value=float(r["sumOpenInterestValue"]))
                for path, key, fld in (
                    ("/futures/data/globalLongShortAccountRatio", "ls_ratio", "longShortRatio"),
                    ("/futures/data/topLongShortPositionRatio", "top_ls", "longShortRatio"),
                    ("/futures/data/takerlongshortRatio", "taker_ratio", "buySellRatio"),
                ):
                    for r in await get(path, {"symbol": sym, "period": "1h", "limit": hours}):
                        try:
                            rows.setdefault(int(r["timestamp"]), {})[key] = float(r[fld])
                        except (KeyError, ValueError, TypeError):
                            continue
                    await asyncio.sleep(0.15)
                await db.crowd_upsert([
                    (sym, t, d.get("oi"), d.get("oi_value"), d.get("ls_ratio"), d.get("top_ls"), d.get("taker_ratio"))
                    for t, d in rows.items()
                ])
                flast = await db.funding_last_t(sym)
                fr = await get("/fapi/v1/fundingRate", {"symbol": sym, "limit": 100 if flast is None else 10})
                await db.funding_upsert([(sym, int(r["fundingTime"]), float(r["fundingRate"])) for r in fr if "fundingRate" in r])
                await asyncio.sleep(0.15)
            # funding radarı: kalabalık verisi toplanmayan diğer coinler için yalnızca funding
            done = set(syms)
            for sym in self.funding_symbols():
                if sym in done:
                    continue
                flast = await db.funding_last_t(sym)
                fr = await get("/fapi/v1/fundingRate", {"symbol": sym, "limit": 100 if flast is None else 10})
                await db.funding_upsert([(sym, int(r["fundingTime"]), float(r["fundingRate"])) for r in fr if "fundingRate" in r])
                await asyncio.sleep(0.1)

    async def _fetch(self, s: aiohttp.ClientSession, base: str, path: str, params: dict) -> list:
        for attempt in range(3):
            try:
                async with s.get(base + path, params=params) as r:
                    if r.status in (418, 429):
                        await asyncio.sleep(int(r.headers.get("Retry-After", "30") or 30))
                        continue
                    if r.status >= 400:
                        return []
                    data = await r.json(content_type=None)
                    return data if isinstance(data, list) else []
            except (aiohttp.ClientError, asyncio.TimeoutError):
                await asyncio.sleep(2 * (attempt + 1))
        return []

    async def _collect_synthetic(self, syms: list[str]) -> None:
        """Geliştirme modu: gerçeğe benzer ama sahte kalabalık verisi (yalnızca arayüz/test için)."""
        db = self.ctx.db
        eng = self.ctx.engine
        now = now_ms() - now_ms() % HOUR
        for sym in syms:
            last = await db.crowd_last_t(sym)
            start = now - 500 * HOUR if last is None else last + HOUR
            if start > now:
                continue
            buf = eng.market.buffer(sym, "1h") if eng else None
            closes = buf.arrays()["c"] if buf is not None and len(buf) else np.array([100.0])
            rng = self._rng
            rows = []
            oi = 1_000_000.0
            for i, t in enumerate(range(start, now + 1, HOUR)):
                price = float(closes[min(len(closes) - 1, max(0, len(closes) - 1 - (now - t) // HOUR))])
                oi *= math.exp(rng.gauss(0, 0.01))
                rows.append((sym, t, oi, oi * price, max(0.3, 1.6 + rng.gauss(0, 0.25)), max(0.3, 1.2 + rng.gauss(0, 0.2)),
                             max(0.2, 1.0 + rng.gauss(0, 0.15))))
            await db.crowd_upsert(rows)
            flast = await db.funding_last_t(sym)
            fstart = (now - 60 * DAY) if flast is None else flast + WINDOW_MS
            fstart -= fstart % WINDOW_MS
            mu = 0.00005 + (hash(sym) % 7) * 0.00003
            await db.funding_upsert([(sym, t, mu + rng.gauss(0, 0.00005)) for t in range(fstart, now + 1, WINDOW_MS)])

    # --- funding radarı ---------------------------------------------------------
    async def carry_settings(self) -> dict:
        return {**DEFAULT_CARRY, **(await self.ctx.db.kv_get("carry_settings", {}) or {})}

    async def _window_sums(self, since: int) -> tuple[dict[str, dict[int, float]], dict[str, list[tuple[int, float]]]]:
        rows = await self.ctx.db.funding_rows(since)
        sums: dict[str, dict[int, float]] = {}
        raw: dict[str, list[tuple[int, float]]] = {}
        for sym, t, r in rows:
            w = t - t % WINDOW_MS
            sums.setdefault(sym, {})[w] = sums.get(sym, {}).get(w, 0.0) + r
            raw.setdefault(sym, []).append((t, r))
        return sums, raw

    @staticmethod
    def _trailing(ws: dict[int, float], w: int, k: int) -> Optional[float]:
        vals = [ws.get(w - i * WINDOW_MS) for i in range(1, k + 1)]
        if any(v is None for v in vals):
            return None
        return float(np.mean(vals)) * PER_YEAR * 100.0

    async def _spot_lookup(self, cs: dict):
        """Sembol → spot bacağının kurulabileceği borsalar. Uygulanan analizin evrenine göre:
        'cross' ise Binance + Bybit + OKX + Gate, değilse yalnızca Binance."""
        from ..market.spot import spot_base, spot_bases, spot_bases_multi
        syn = bool(getattr(self.ctx, "synthetic", False))
        if cs.get("universe") == "cross":
            multi = await spot_bases_multi(self.ctx.db, syn)
            if multi is None:
                return lambda s: ["?"]
            return lambda s: [n for n, b in multi.items() if spot_base(s) in b]
        bases = await spot_bases(self.ctx.db, syn)
        if bases is None:
            return lambda s: ["Binance"]
        return lambda s: ["Binance"] if spot_base(s) in bases else []

    async def radar(self) -> dict:
        cs = await self.carry_settings()
        where_of = await self._spot_lookup(cs)
        now = now_ms()
        sums, raw = await self._window_sums(now - 35 * DAY)
        w_now = now - now % WINDOW_MS
        eng = self.ctx.engine
        paper = await self.ctx.db.kv_get("carry_paper", None)
        held = set((paper or {}).get("holdings", []))
        rows = []
        trailing_now: dict[str, float] = {}
        syms = set(self.symbols()) | set(sums)
        for sym in syms:
            ws = sums.get(sym, {})
            tr = self._trailing(ws, w_now, int(cs["k"]))
            where = where_of(sym)
            spot_ok = bool(where)
            if tr is not None and spot_ok:
                trailing_now[sym] = tr
            mi = eng.market.funding(sym) if eng else None
            cur = float(mi.funding_rate) if mi else None
            pts = raw.get(sym, [])
            interval_h = 8.0
            if len(pts) >= 3:
                d = np.diff([p[0] for p in pts[-6:]])
                if len(d):
                    interval_h = max(1.0, float(np.median(d)) / HOUR)

            def apr_days(days: int) -> Optional[float]:
                xs = [r for t, r in pts if t >= now - days * DAY]
                if len(xs) < 3:
                    return None
                return float(np.sum(xs)) / days * 365 * 100.0

            rows.append({
                "symbol": sym,
                "current_rate": cur,
                "current_apr_pct": (cur * (365 * 24 / interval_h) * 100.0) if cur is not None else None,
                "next_funding_time": mi.next_funding_time if mi else None,
                "interval_hours": interval_h,
                "apr_7d_pct": apr_days(7),
                "apr_30d_pct": apr_days(30),
                "trailing_apr_pct": tr,
                "held": sym in held,
                "spot": spot_ok,
                "spot_where": where,
            })
        pick = portfolio_step(trailing_now, held, int(cs["top_n"]), float(cs["enter_apr"]), float(cs["exit_apr"]))
        for r in rows:
            r["selected"] = r["symbol"] in pick
        rows.sort(key=lambda r: -(r["trailing_apr_pct"] if r["trailing_apr_pct"] is not None else -1e9))
        return {"settings": cs, "rows": rows, "paper": self._paper_summary(paper, cs), "updated_at": self.last_run}

    # --- sanal funding portföyü (canlı ileri test) --------------------------------
    @staticmethod
    def _paper_summary(paper: Optional[dict], cs: dict) -> Optional[dict]:
        if not paper:
            return None
        hist = paper.get("history", [])
        days = max(1e-9, (paper.get("last_window", paper["started_at"]) - paper["started_at"]) / DAY)
        cum = float(paper.get("cum", 0.0))
        return {
            "started_at": paper["started_at"], "windows": len(hist), "return_pct": cum * 100.0,
            "apr_pct": cum / days * 365 * 100.0 if days >= 1 else None,
            "holdings": paper.get("holdings", []), "history": hist[-90:], "capital": 1000.0,
            "equity": 1000.0 * (1 + cum),
        }

    async def carry_paper_update(self) -> None:
        db = self.ctx.db
        cs = await self.carry_settings()
        now = now_ms()
        paper = await db.kv_get("carry_paper", None)
        w_now = now - now % WINDOW_MS
        if not paper:
            paper = {"started_at": w_now, "last_window": w_now, "holdings": [], "cum": 0.0, "history": []}
            await db.kv_set("carry_paper", paper, now)
            return
        k = int(cs["k"])
        top_n = int(cs["top_n"])
        eff = 1.0 / (1.0 + 1.0 / float(cs["leverage"]))
        half = (float(cs["spot_fee"]) + float(cs["perp_fee"]) + 2 * float(cs["slippage"]))
        sums, _ = await self._window_sums(paper["last_window"] - (k + 2) * WINDOW_MS)
        where_of = await self._spot_lookup(cs)
        sums = {s: v for s, v in sums.items() if where_of(s)}  # yalnızca spot bacağı kurulabilen coinler
        changed = False
        w = paper["last_window"]
        # bir pencere, tamamen bitip ödemeleri kaydedildikten sonra işlenir
        while w + WINDOW_MS + 10 * 60_000 <= now:
            trailing = {}
            for sym, ws in sums.items():
                if w in ws:
                    tr = self._trailing(ws, w, k)
                    if tr is not None:
                        trailing[sym] = tr
            old = set(paper["holdings"])
            new = portfolio_step(trailing, old, top_n, float(cs["enter_apr"]), float(cs["exit_apr"]))
            if new != old and w + 2 * WINDOW_MS > now:  # yalnızca güncel dönem için bildir (geriye dönük işlemede değil)
                entered = sorted(new - old)
                left = sorted(old - new)
                parts = []
                if entered:
                    parts.append("Girilecek: " + ", ".join(f"{x.replace('USDT', '')} (yıllık %{trailing.get(x, 0):.0f})" for x in entered))
                if left:
                    parts.append("Çıkılacak: " + ", ".join(x.replace("USDT", "") for x in left))
                notifier = getattr(self.ctx, "notifier", None)
                if notifier is not None and parts:
                    try:
                        notifier.notify("💰 <b>Funding radarı</b>\n" + "\n".join(parts), kind="system")
                    except Exception:
                        pass
            slot = 1.0 / top_n
            ret = -len(new ^ old) * slot * half * eff
            got = {}
            for sym in new:
                r = sums.get(sym, {}).get(w, 0.0)
                got[sym] = r
                ret += slot * r * eff
            paper["cum"] = float(paper["cum"]) + ret
            paper["holdings"] = sorted(new)
            paper["history"] = (paper["history"] + [{"t": w, "ret_pct": ret * 100.0, "holdings": sorted(new),
                                                      "funding": {s: v for s, v in got.items()}}])[-500:]
            w += WINDOW_MS
            paper["last_window"] = w
            changed = True
        if changed:
            await db.kv_set("carry_paper", paper, now)

    async def reset_paper(self) -> None:
        await self.ctx.db.kv_delete("carry_paper")
        await self.carry_paper_update()

    # --- kendi kendini eğiten model: canlı sanal takip + aylık otomatik yeniden eğitim ----------
    async def _ml_panel(self, days: int):
        mgr = getattr(self.ctx, "backtests", None)
        if mgr is None:
            return None
        syms = mgr._all_perps()
        return await mgr.load_ml_inputs(syms, days, lambda f, m: None, lambda: False)

    async def ml_paper_update(self, force: bool = False) -> None:
        """Günde bir (UTC gün kapanışından sonra): önceki pozisyonların getirisini işler, gerekirse yeniden dengeler.
        Model 30 günden eskiyse önce tüm veriyle kendini yeniden eğitir. Kurallar geçmiş testtekiyle aynıdır:
        oynaklığa ters orantılı ağırlıklar ve portföy oynaklığı hedefleme."""
        import math

        from ..ml import model as M
        from ..ml.features import forward_returns
        db = self.ctx.db
        m = await db.kv_get("ml_model", None)
        if not m:
            return
        now = now_ms()
        today = now - now % DAY
        paper = await db.kv_get("ml_paper", None)
        if paper and paper.get("last_day") == today and not force:
            return
        if (now % DAY) < 20 * 60_000 and not force:
            return  # günlük mumların oturması için gece yarısından sonra biraz bekle
        h = int(m.get("hold_days", 1))
        n = int(m.get("top_n", 8))
        kind = m.get("kind") or ("ridge" if m.get("type") == "ridge" else "gbm")
        # aylık otomatik yeniden eğitim (tüm veriyle, aynı model türü)
        if now - int(m.get("retrained_at", 0)) > 30 * DAY:
            big = await self._ml_panel(1000)
            if big is not None and len(big.t) > 200:
                fwd = forward_returns(big.close, h)
                X, y = await asyncio.to_thread(M._training_set, big, fwd, len(big.t) - h - 1, h)
                if len(y) >= 300:
                    fit_kind = kind if kind in M.MODELS else ("ridge300" if m.get("type") == "ridge" else "gbm")
                    try:
                        new = await asyncio.to_thread(M.fit, fit_kind, X, y)
                        new = {k: v for k, v in new.items() if k != "_obj"}
                        m.update(new)
                        m["retrained_at"] = now
                        m["trained_until"] = int(big.t[-1])
                        await db.kv_set("ml_model", m, now)
                        log.info("Model kendini yeniden eğitti (%d gözlem)", len(y))
                        self.ctx.notifier.notify("🤖 <b>Yapay zeka modeli</b> son verilerle kendini yeniden eğitti.", kind="system")
                    except Exception:
                        log.exception("Model yeniden eğitilemedi")
        panel = await self._ml_panel(400)
        if panel is None or len(panel.t) < 120:
            return
        last = len(panel.t) - 1
        day_t = int(panel.t[last])
        if not paper:
            paper = {"started_at": now, "cum": 0.0, "longs": {}, "shorts": {}, "hold_left": 0, "last_day": None,
                     "last_bar": None, "history": [], "raw": []}
        idx = {s_: k for k, s_ in enumerate(panel.syms)}
        target_daily = M.TARGET_ANNUAL_VOL / math.sqrt(365)
        # önceki seçimden bu yana tamamlanan günlerin getirisi
        if paper.get("last_bar") is not None and day_t > paper["last_bar"]:
            d0 = int(np.searchsorted(panel.t, paper["last_bar"]))
            for d in range(d0, last):
                def leg(book: dict) -> float:
                    tot = 0.0
                    for s_, w in book.items():
                        c = idx.get(s_)
                        if c is None:
                            continue
                        a, b = panel.close[d, c], panel.close[d + 1, c]
                        if a > 0 and not np.isnan(b):
                            tot += w * (b / a - 1)
                    return tot
                if paper["longs"]:
                    raw = leg(paper["longs"]) - leg(paper["shorts"])
                    past = np.array(paper.get("raw", [])[-30:])
                    scale = float(np.clip(target_daily / past.std(), 0.3, 1.5)) if len(past) >= 10 and past.std() > 0 else 1.0
                    r = scale * raw
                    paper["raw"] = (paper.get("raw", []) + [raw])[-60:]
                    paper["cum"] = (1 + paper["cum"]) * (1 + r) - 1
                    paper["history"] = (paper["history"] + [{"t": int(panel.t[d + 1]), "ret_pct": r * 100.0}])[-400:]
                paper["hold_left"] = int(paper.get("hold_left", 0)) - 1
        # yeniden dengeleme
        if paper.get("last_bar") != day_t and paper.get("hold_left", 0) <= 0:
            ok = panel.tradable[last]
            if ok.sum() >= 12:
                sc = np.full(len(panel.syms), np.nan)
                sc[ok] = M.predict(m, panel.X[last, ok])
                lw, sw = M.pick(panel, last, sc, n)
                if lw:
                    nl = {panel.syms[c]: w for c, w in lw.items()}
                    ns = {panel.syms[c]: w for c, w in sw.items()}
                    turnover = sum(abs(nl.get(k, 0) - paper["longs"].get(k, 0)) for k in set(nl) | set(paper["longs"]))
                    turnover += sum(abs(ns.get(k, 0) - paper["shorts"].get(k, 0)) for k in set(ns) | set(paper["shorts"]))
                    paper["cum"] = (1 + paper["cum"]) * (1 - turnover * 0.001) - 1
                    if set(nl) != set(paper["longs"]) or set(ns) != set(paper["shorts"]):
                        try:
                            self.ctx.notifier.notify(
                                "🤖 <b>Yapay zeka modeli</b> (sanal)\nLong: " + ", ".join(x.replace("USDT", "") for x in nl)
                                + "\nShort: " + ", ".join(x.replace("USDT", "") for x in ns), kind="system")
                        except Exception:
                            pass
                    paper["longs"], paper["shorts"] = nl, ns
                    paper["hold_left"] = h
        paper["last_bar"] = day_t
        paper["last_day"] = today
        await db.kv_set("ml_paper", paper, now)

    async def ml_status(self) -> dict:
        m = await self.ctx.db.kv_get("ml_model", None)
        p = await self.ctx.db.kv_get("ml_paper", None)
        out: dict[str, Any] = {"active": bool(m)}
        if m:
            out["model"] = {k: m.get(k) for k in ("kind", "lambda", "hold_days", "top_n", "trained_until", "retrained_at", "applied_at", "source_job")}
        if p:
            days = max(0.0, (now_ms() - int(p["started_at"])) / DAY)
            out["paper"] = {"started_at": p["started_at"], "return_pct": float(p["cum"]) * 100.0, "equity": 1000.0 * (1 + float(p["cum"])),
                            "days": days, "long": list(p.get("longs", {}) or p.get("long", [])),
                            "short": list(p.get("shorts", {}) or p.get("short", [])), "history": p.get("history", [])[-60:]}
        return out

