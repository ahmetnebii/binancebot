"""Tur 2: vektörel göstergeler, MTF yardımcıları, kalibrasyonlu EV, backtest değişmezleri, sağlık monitörü, API."""
import asyncio
import math
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.backtest import metrics as M
from app.backtest.data import SyntheticHistory
from app.backtest.report import build_result
from app.backtest.simulator import Backtester, BacktestConfig
from app.features.indicators import aggregate_bars, bollinger, ema, log_volume_z, rma
from app.features.mtf import trend_of
from app.market.synthetic import SPECS
from app.models import Signal, TakeProfit
from app.settings import RiskSettings, ScannerSettings, StrategySettings
from app.strategies.scorer import estimate
from app.util import now_ms

H = {"X-BB-Client": "ui"}


def _ema_loop(x, n):
    out = np.full(len(x), np.nan)
    a = 2 / (n + 1)
    p = x[:n].mean()
    out[n - 1] = p
    for i in range(n, len(x)):
        p = a * x[i] + (1 - a) * p
        out[i] = p
    return out


def _rma_loop(x, n):
    out = np.full(len(x), np.nan)
    p = x[:n].mean()
    out[n - 1] = p
    for i in range(n, len(x)):
        p = (p * (n - 1) + x[i]) / n
        out[i] = p
    return out


@pytest.mark.parametrize("scale", [1e-6, 1.0, 7e4])
@pytest.mark.parametrize("n", [2, 14, 50, 200])
def test_vectorized_ewm_matches_loop(scale, n):
    rng = np.random.default_rng(n)
    x = scale * np.exp(np.cumsum(rng.normal(0, 0.01, 12000)))
    for fast, slow in ((ema, _ema_loop), (rma, _rma_loop)):
        a, b = fast(x, n), slow(x, n)
        m = ~np.isnan(b)
        assert np.isnan(a[~m]).all()
        assert np.max(np.abs(a[m] - b[m]) / np.abs(b[m])) < 1e-10


def test_bollinger_matches_window_stats():
    rng = np.random.default_rng(5)
    c = 68000 + np.cumsum(rng.normal(0, 40, 600))
    mid, up, lo, _ = bollinger(c, 20, 2.0)
    w = np.lib.stride_tricks.sliding_window_view(c, 20)
    assert np.allclose(mid[19:], w.mean(1), rtol=0, atol=1e-6)
    assert np.allclose((up[19:] - mid[19:]) / 2, w.std(1), rtol=0, atol=1e-5)


def test_aggregate_bars_and_mtf_trend():
    t = (np.arange(16) * 900_000).astype(np.int64)
    a = {"t": t, "o": np.arange(16.0), "h": np.arange(16.0) + 1, "l": np.arange(16.0) - 1, "c": np.arange(16.0) + 0.5,
         "v": np.ones(16), "qv": np.ones(16), "tbq": np.ones(16) * 0.5}
    h = aggregate_bars(a, 3_600_000)
    assert len(h["t"]) == 4 and h["o"][1] == 4.0 and h["c"][1] == 7.5 and h["h"][1] == 8.0 and h["qv"][0] == 4.0
    partial = aggregate_bars({k: v[:14] for k, v in a.items()}, 3_600_000)
    assert len(partial["t"]) == 3  # tamamlanmamış son saat atılır
    up = np.linspace(100, 200, 300)
    assert trend_of(up)[0] == "up" and trend_of(up[::-1])[0] == "down"
    assert trend_of(100 + np.sin(np.arange(300) / 3.0))[0] in ("flat", "up", "down")


def test_log_volume_z_flags_spike():
    rng = np.random.default_rng(1)
    v = np.exp(rng.normal(10, 0.3, 120))
    assert abs(log_volume_z(v, 96)) < 4
    v[-1] = v[:-1].mean() * 8
    assert log_volume_z(v, 96) > 4


def _sig(strategy="S1", score=75.0, tps=((1.5, 0.4), (3.0, 0.3))):
    return Signal(
        id="s", symbol="X", side="LONG", strategy=strategy, strategy_name="x", timeframe="15m", created_at=0,
        expires_at=1, bar_time=0, entry=100.0, stop=98.0, score=score,
        take_profits=[TakeProfit(r=r, price=100 + 2 * r, fraction=f) for r, f in tps],
    )


def test_calibration_moves_expected_value():
    base_p, base_ev = estimate(_sig(), 0.0005, 0.0005, 0.0)
    good = {"S1": {"n": 150, "win_rate": 0.55, "avg_win_r": 2.2, "avg_loss_r": 0.9, "expectancy_r": 0.8}}
    bad = {"S1": {"n": 150, "win_rate": 0.25, "avg_win_r": 1.5, "avg_loss_r": 1.0, "expectancy_r": -0.4}}
    p_good, ev_good = estimate(_sig(), 0.0005, 0.0005, 0.0, calibration=good)
    p_bad, ev_bad = estimate(_sig(), 0.0005, 0.0005, 0.0, calibration=bad)
    assert p_bad < base_p < p_good and ev_bad < base_ev < ev_good
    few = {"S1": {"n": 5, "win_rate": 0.9, "avg_win_r": 5, "avg_loss_r": 0.1, "expectancy_r": 4}}
    assert estimate(_sig(), 0.0005, 0.0005, 0.0, calibration=few) == (base_p, base_ev)  # n<10 yok sayılır
    _, ev_s3 = estimate(_sig("S3", tps=((1.2, 1.0),)), 0.0005, 0.0005, 0.0)
    assert ev_s3 < 1.2  # tek hedefli stratejide kazanç hedef R ile sınırlı


@pytest.fixture(scope="module")
def bt_run():
    syms = [s[0] for s in SPECS][:10]
    end = now_ms()
    end -= end % 900_000
    start = end - 30 * 86_400_000
    data = asyncio.run(SyntheticHistory().load(syms, start - 12 * 86_400_000, end, lambda f, m: None, lambda: False))
    risk = RiskSettings()
    cfg = BacktestConfig(symbols=syms, start_ms=start, end_ms=end, initial_equity=1000.0, s1=True, s2=True, s3=True,
                         slippage_bps=3.0, taker_fee=0.0005, maker_fee=0.0002, risk=risk,
                         strategies=StrategySettings(s3_enabled=True), scanner=ScannerSettings())
    raw = Backtester(cfg, data, {}, lambda f, m: None, lambda: False).run()
    raw2 = Backtester(cfg, data, {}, lambda f, m: None, lambda: False).run()
    res = build_result(raw, initial_equity=1000.0, start_ms=start, end_ms=end, source="synthetic", extra_warnings=[])
    return raw, raw2, res, risk


def test_backtest_is_deterministic(bt_run):
    raw, raw2, _, _ = bt_run
    assert len(raw["trades"]) == len(raw2["trades"]) > 10
    assert [round(t["pnl"], 9) for t in raw["trades"]] == [round(t["pnl"], 9) for t in raw2["trades"]]


def test_backtest_invariants(bt_run):
    raw, _, res, risk = bt_run
    trades = raw["trades"]
    by_sym: dict[str, list] = {}
    for t in trades:
        assert math.isfinite(t["r"]) and math.isfinite(t["pnl"])
        assert t["risk_frac"] <= risk.max_risk_pct * 1.05  # kayma payıyla
        assert t["exit_time"] >= t["entry_time"]
        by_sym.setdefault(t["symbol"], []).append(t)
        if t["reason"] == "Stop-loss":
            assert -1.4 < t["r"] < -0.7
    for items in by_sym.values():  # aynı sembolde çakışan pozisyon yok
        items.sort(key=lambda x: x["entry_time"])
        for a, b in zip(items, items[1:]):
            assert b["entry_time"] >= a["exit_time"]
    events = sorted([(t["entry_time"], 1) for t in trades] + [(t["exit_time"], -1) for t in trades], key=lambda e: (e[0], e[1]))
    cur = peak = 0
    for _, d in events:
        cur += d
        peak = max(peak, cur)
    assert peak <= risk.max_positions
    s = res["summary"]
    assert abs((s["final_equity"] - s["initial_equity"]) - sum(t["pnl"] for t in trades)) < 1e-6 * s["initial_equity"]
    assert sum(b["count"] for b in res["r_histogram"]) == len(trades)
    mc = res["monte_carlo"]
    assert mc is not None and mc["dd_p50"] <= mc["dd_p95"] <= mc["dd_p99"]
    assert all(p["drawdown_pct"] <= 1e-9 for p in res["equity"])
    assert len(res["walk_forward"]) == 3 and s["max_drawdown_pct"] >= 0


def test_metrics_helpers():
    assert M.trade_stats([], [])["win_rate"] is None
    st = M.trade_stats([2.0, -1.0, -1.0, 1.0], [20.0, -10.0, -10.0, 10.0])
    assert st["win_rate"] == 0.5 and st["profit_factor"] == 1.5 and st["expectancy_r"] == 0.25
    months = M.monthly_pnl([(1_700_000_000_000, 5.0), (1_700_000_000_000, -2.0)])
    assert months == [{"month": "2023-11", "pnl": 3.0, "trades": 2}]


async def test_health_monitor_disables_failing_strategy(db, store):
    from app.engine.engine import Engine
    from app.events import EventBus, EventLog
    from app.models import Position
    from app.notify.telegram import TelegramNotifier
    from app.risk.manager import RiskManager
    from tests.conftest import FakeMarket

    for i in range(20):
        p = Position(id=f"h{i}", env="paper", symbol="X", side="LONG", strategy="S1", strategy_name="Trend Kırılımı",
                     opened_at=i, closed_at=1000 + i, status="CLOSED", entry_price=1, qty=0, initial_qty=1, leverage=1,
                     margin=1, notional=1, stop_price=0.9, initial_stop=0.9, r_final=-0.5)
        await db.save_position(p)
    bus = EventBus()
    m = FakeMarket()
    eng = Engine(db=db, store=store, bus=bus, events=EventLog(db, bus), risk=RiskManager(store, db),
                 notifier=TelegramNotifier(store), market_factory=lambda env: m)
    assert store.strategies.s1_enabled
    await eng._check_strategy_health("S1")
    assert not store.strategies.s1_enabled


def test_api_analysis_and_backtest(tmp_path, monkeypatch):
    monkeypatch.setenv("BB_DATA_SOURCE", "synthetic")
    monkeypatch.setenv("BB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BB_FRONTEND_DIST", str(tmp_path / "nofrontend"))
    from app.api.app import create_app

    with TestClient(create_app()) as c:
        c.post("/api/auth/setup", json={"password": "uzun-ve-guclu-1"}, headers=H)
        time.sleep(4)
        a = c.get("/api/market/analysis", params={"symbol": "SOLUSDT"}).json()
        assert [t["tf"] for t in a["timeframes"]] == ["15m", "1h", "4h"]
        assert a["confluence"]["label"] and a["liquidity"]["spread_bps"] is not None and a["notes"]
        assert c.get("/api/market/analysis", params={"symbol": "YOKUSDT"}).status_code == 404
        assert c.post("/api/backtest/run", json={"days": 3}, headers=H).status_code == 422
        job = c.post("/api/backtest/run", json={"days": 14, "top_n": 6}, headers=H).json()
        t0 = time.time()
        while time.time() - t0 < 60:
            j = c.get(f"/api/backtest/jobs/{job['id']}").json()
            if j["status"] in ("done", "failed"):
                break
            time.sleep(0.5)
        assert j["status"] == "done", j.get("error")
        assert j["result"]["data_source"] == "synthetic" and j["summary"]["trades"] == j["result"]["summary"]["trades"]
        listed = c.get("/api/backtest/jobs").json()
        assert listed[0]["id"] == job["id"] and "result" not in listed[0]
        cal = c.post(f"/api/backtest/jobs/{job['id']}/apply-calibration", headers=H)
        if j["result"]["calibration"]:
            assert cal.status_code == 200 and c.get("/api/backtest/calibration").json()["source_job"] == job["id"]
        else:
            assert cal.status_code == 400
        assert c.delete("/api/backtest/calibration", headers=H).json()["strategies"] == {}
        assert c.delete(f"/api/backtest/jobs/{job['id']}", headers=H).json() == {"ok": True}
        st = c.get("/api/history/stats").json()
        assert "r_histogram" in st and "monthly" in st
