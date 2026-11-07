"""Tek tuşla tam analiz: rapor sıralaması ve uçtan uca ana iş."""
import time

from fastapi.testclient import TestClient

from app.backtest import lab
from app.backtest.full import build_report

H = {"X-BB-Client": "ui"}
DAY = 86_400_000


def _lab_res(verdict, ret, fw=False):
    return {"period": {"split2": 0, "end": 200 * DAY},
            "groups": [{"name": "Büyük coinler", "verdict": verdict, "forward_ok": fw, "full_summary": {"total_return_pct": 50},
                        "best": {"label": "Kural", "details": ["a"], "final": {"return_pct": ret, "max_drawdown_pct": 2, "trades": 30, "expectancy_r": 0.1}}}]}


def _carry_res(verdict, apr):
    t = {"apr_pct": apr, "return_pct": apr, "max_drawdown_pct": 0.2, "positive_months_pct": 90, "time_in_pct": 95}
    return {"verdict": verdict, "period": {"split": 0, "end": 400 * DAY}, "costs": {"round_trip_pct": 0.3, "leverage": 3},
            "portfolio": {"test": t, "params": {"top_n": 5, "k": 21, "enter_apr": 10}}, "coins": [], "coins_used": 20}


def test_report_prefers_validated_over_higher_unvalidated():
    steps = [{"key": "lab_groups", "name": "Lab", "job_id": "l1"}, {"key": "carry_all", "name": "Funding arbitrajı · tüm", "job_id": "c1"}]
    r = build_report(steps, {"lab_groups": _lab_res("none", 30.0, fw=True), "carry_all": _carry_res("weak", 4.0)}, None)
    assert r["best"]["kind"] == "carry" and r["best"]["oos_apr_pct"] == 4.0  # doğrulanmış ama düşük olan önce
    assert r["trial"]["kind"] == "lab"  # doğrulanmamış ama umut verici aday deneme önerisi olarak
    r2 = build_report(steps, {"lab_groups": _lab_res("none", -5.0), "carry_all": _carry_res("none", 1.0)}, None)
    assert r2["best"] is None and "avantaj yok" in r2["summary"]
    lab_c = next(c for c in r["candidates"] if c["kind"] == "lab")
    assert abs(lab_c["oos_apr_pct"] - 30.0 / 200 * 365) < 1e-6  # final sınavı getirisi yıllığa çevrilir


def test_full_job_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("BB_DATA_SOURCE", "synthetic")
    monkeypatch.setenv("BB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BB_FRONTEND_DIST", str(tmp_path / "nofrontend"))
    monkeypatch.setitem(lab.BUDGETS, "derin", 40)
    monkeypatch.setitem(lab.BUDGETS, "normal", 20)
    import app.backtest.full as full
    steps = [list(s) for s in full.STEPS]
    for s in steps:
        if s[3] in ("lab", "carry", "data"):
            s[4] = {**s[4], "days": 120 if s[3] != "carry" else 200}
        if s[0] == "lab_coins":
            s[4]["top_n"] = 3
    monkeypatch.setattr(full, "STEPS", [tuple(s) for s in steps])
    import app.backtest.manager as mgr
    monkeypatch.setattr(mgr, "FULL_STEPS", [tuple(s) for s in steps])
    from app.api.app import create_app

    with TestClient(create_app()) as c:
        c.post("/api/auth/setup", json={"password": "uzun-ve-guclu-1"}, headers=H)
        time.sleep(3)
        j = c.post("/api/backtest/full", headers=H).json()
        assert c.post("/api/backtest/full", headers=H).status_code == 409  # aynı anda ikinci iş yok
        t0 = time.time()
        while time.time() - t0 < 600:
            r = c.get(f"/api/backtest/jobs/{j['id']}").json()
            if r["status"] not in ("queued", "running"):
                break
            time.sleep(1)
        assert r["status"] == "done", r.get("error")
        steps = r["config"]["steps"]
        assert [s["status"] for s in steps] == ["done"] * 7, steps
        rep = r["result"]
        assert rep["summary"] and isinstance(rep["candidates"], list) and len(rep["candidates"]) >= 3
        assert {c_["kind"] for c_ in rep["candidates"]} == {"lab", "carry"}
        # alt işler kendi sekmelerinde de görünür
        assert len(c.get("/api/backtest/lab/jobs").json()) == 2 and len(c.get("/api/backtest/carry/jobs").json()) == 3
        assert len(c.get("/api/backtest/full/jobs").json()) == 1
        carry_c = next(x for x in rep["candidates"] if x["kind"] == "carry")
        assert carry_c["monthly"] and all(isinstance(m["month"], str) for m in carry_c["monthly"])
        # eski rapor (aylık veri olmadan kaydedilmiş) okunurken aylık getiriler sonradan eklenir
        import asyncio
        ctx = c.app.state.ctx
        stripped = {**rep, "candidates": [{k: v for k, v in x.items() if k != "monthly"} for x in rep["candidates"]]}
        job_meta = {k: v for k, v in r.items() if k != "result"}
        c.portal.call(ctx.db.bt_save, job_meta, stripped)
        again = c.get(f"/api/backtest/jobs/{j['id']}").json()["result"]
        assert all("monthly" in x for x in again["candidates"])
        assert next(x for x in again["candidates"] if x["kind"] == "carry")["monthly"] == carry_c["monthly"]
