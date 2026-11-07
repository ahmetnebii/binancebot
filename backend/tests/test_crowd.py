"""Kalabalık verisi toplayıcı, sanal funding portföyü ve kalabalık analizi."""
import asyncio
from types import SimpleNamespace

import numpy as np

from app.backtest.carry import WINDOW_MS
from app.crowd import analysis
from app.crowd.collector import HOUR, CrowdCollector
from app.db import Database
from app.util import now_ms


class FakeCollector(CrowdCollector):
    def __init__(self, db, syms):
        ctx = SimpleNamespace(db=db, synthetic=False, engine=None,
                              store=SimpleNamespace(connection=SimpleNamespace(live_rest="http://x")))
        super().__init__(ctx)
        self._syms = syms
        self.requests = []

    def symbols(self):
        return self._syms

    async def _fetch(self, s, base, path, params):
        self.requests.append((path, params.get("limit")))
        now = now_ms() - now_ms() % HOUR
        n = params.get("limit", 3)
        ts = [now - i * HOUR for i in range(min(n, 500))][::-1]
        if path.endswith("openInterestHist"):
            return [{"timestamp": t, "sumOpenInterest": "100", "sumOpenInterestValue": "2500"} for t in ts]
        if path.endswith("globalLongShortAccountRatio") or path.endswith("topLongShortPositionRatio"):
            return [{"timestamp": t, "longShortRatio": "1.8"} for t in ts]
        if path.endswith("takerlongshortRatio"):
            return [{"timestamp": t, "buySellRatio": "1.1"} for t in ts]
        if path.endswith("fundingRate"):
            w = now - now % WINDOW_MS
            return [{"fundingTime": w - i * WINDOW_MS, "fundingRate": "0.0001"} for i in range(n)][::-1]
        return []


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_collector_backfills_then_increments(tmp_path):
    async def go():
        db = Database(tmp_path / "t.sqlite3")
        await db.open()
        c = FakeCollector(db, ["AAAUSDT"])
        await c.collect()
        first = [r for r in c.requests if "openInterest" in r[0]][0]
        assert first[1] == 500  # ilk sefer ~20 gün geri doldurma
        cov = await db.crowd_coverage()
        assert cov["rows"] == 500 and cov["funding_rows"] == 100
        c.requests.clear()
        await c.collect()
        assert [r for r in c.requests if "openInterest" in r[0]][0][1] <= 3  # sonra yalnızca son saatler
        rows = await db.crowd_rows(0, "AAAUSDT")
        assert rows[-1][4] == 1.8 and rows[-1][3] == 2500
        await db.close()
    asyncio.run(go())


def test_paper_carry_follows_rules(tmp_path):
    async def go():
        db = Database(tmp_path / "p.sqlite3")
        await db.open()
        now = now_ms()
        w_now = now - now % WINDOW_MS
        rows = []
        for i in range(90):  # 30 gün
            t = w_now - (90 - i) * WINDOW_MS
            rows += [("HIGHUSDT", t, 0.0005), ("NEGUSDT", t, -0.0002), ("LOWUSDT", t, 0.00001)]
        await db.funding_upsert(rows)
        c = FakeCollector(db, [])
        await db.kv_set("carry_settings", {"k": 9, "enter_apr": 10.0, "exit_apr": 0.0, "top_n": 2})
        start = w_now - 20 * WINDOW_MS
        await db.kv_set("carry_paper", {"started_at": start, "last_window": start, "holdings": [], "cum": 0.0, "history": []})
        await c.carry_paper_update()
        p = await db.kv_get("carry_paper")
        assert p["holdings"] == ["HIGHUSDT"]  # yalnızca eşiğin üstündeki coin; negatif funding'e girilmez
        assert p["cum"] > 0 and len(p["history"]) >= 18
        # tekrar çalıştırmak aynı pencereyi iki kez saymaz
        before = p["cum"]
        await c.carry_paper_update()
        assert (await db.kv_get("carry_paper"))["cum"] == before
        radar = await c.radar()
        top = radar["rows"][0]
        assert top["symbol"] == "HIGHUSDT" and top["held"] and top["selected"] and top["apr_30d_pct"] > 40
        await db.close()
    asyncio.run(go())


def test_crowd_analysis_detects_contrarian_effect():
    rng = np.random.default_rng(3)
    rows = []
    t0 = 1_700_000_000_000 - 1_700_000_000_000 % HOUR
    for s in ("AUSDT", "BUSDT", "CUSDT", "DUSDT"):
        price = 100.0
        ls = np.empty(2400)
        x = 0.0
        for i in range(2400):  # kalabalık eğilimi kalıcıdır (günlerce sürebilir)
            x = 0.98 * x + rng.normal(0, 0.05)
            ls[i] = 1.5 + x
        for i in range(2400):
            # kalabalık normalden fazla long iken fiyat sonraki saatlerde düşüyor (yapay etki)
            drift = -0.0008 * (ls[i - 1] - 1.5) / 0.2 if i >= 1 else 0.0
            price *= np.exp(drift + rng.normal(0, 0.003))
            rows.append((s, t0 + i * HOUR, 100.0, 100.0 * price, float(ls[i]), 1.0, 1.0))
    res = analysis.analyze(rows, [])
    r = next(x for x in res["results"] if x["feature"] == "ls_ratio")
    assert res["enough"] and r["diff_pct"] < 0 and r["diff_t"] < -2 and "tersi" in r["reading"]
