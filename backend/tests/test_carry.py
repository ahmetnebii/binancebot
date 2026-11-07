"""Funding arbitrajı analizi: pencere toplama, gelecek bilgisi yok, maliyetler, uçtan uca."""
import asyncio

import numpy as np
import pytest

from app.backtest import carry
from app.backtest.data import HistoryData, SyntheticHistory
from app.market.synthetic import SPECS
from app.util import now_ms

W = carry.WINDOW_MS


def _data(rates_by_sym, start, interval=W):
    d = HistoryData(source="synthetic")
    for s, rates in rates_by_sym.items():
        t = start + interval + np.arange(len(rates), dtype=np.int64) * interval
        d.funding[s] = (t, np.asarray(rates, float))
    return d


def test_funding_matrix_sums_4h_payments():
    start = 1_700_006_400_000 - (1_700_006_400_000 % W)
    d = _data({"A": [0.0001] * 12}, start, interval=W // 2)  # 4 saatte bir ödeme
    w, R, syms = carry.funding_matrix(d, ["A"], start, start + 6 * W)
    assert syms == ["A"]
    assert np.allclose(R[0][1:6], 0.0002)  # her 8 saatlik pencerede iki ödeme


def test_trailing_mean_is_causal():
    row = np.array([1.0, 2.0, 3.0, 4.0, 100.0])
    m = carry._trailing_mean(row, 2)
    assert np.isnan(m[0]) and np.isnan(m[1]) and m[2] == 1.5 and m[3] == 2.5 and m[4] == 3.5  # 100 kendi penceresinde görünmez


def test_single_rule_costs_and_entry():
    row = np.array([0.001] * 30)  # yüksek funding
    ret, held = carry.run_single(row, 3, 10.0, 0.0, cost_rt=0.004, eff=0.75)
    assert not held[:3].any() and held[3:].all()  # 3 dönem bilgi biriktikten sonra girer
    assert ret[3] == pytest.approx(0.001 * 0.75 - 0.002 * 0.75)  # ilk dönem: funding − giriş maliyeti
    assert ret[10] == pytest.approx(0.001 * 0.75)
    neg = np.array([-0.0005] * 30)
    r2, h2 = carry.run_single(neg, 3, 10.0, 0.0, cost_rt=0.004, eff=0.75)
    assert not h2.any() and r2.sum() == 0  # negatif funding'de hiç girmez


def test_portfolio_has_no_lookahead():
    rng = np.random.default_rng(1)
    R = rng.normal(0.0001, 0.0002, (6, 200))
    r1, _, h1 = carry.run_portfolio(R, 9, 5.0, 0.0, 2, 0.004, 0.75)
    R2 = R.copy()
    R2[:, 150:] = rng.normal(0.01, 0.001, (6, 50))  # geleceği değiştir
    r2, _, h2 = carry.run_portfolio(R2, 9, 5.0, 0.0, 2, 0.004, 0.75)
    assert h1[:150] == h2[:150] and np.allclose(r1[:150], r2[:150])


def test_analyze_end_to_end():
    syms = [s[0] for s in SPECS][:8]
    end = now_ms()
    start = end - 300 * 86_400_000
    data = asyncio.run(SyntheticHistory().load(syms, start, end, lambda f, m: None, lambda: False))
    r = carry.analyze(data, syms, start, end)
    p = r["portfolio"]
    assert r["verdict"] in ("recommended", "weak", "none")
    assert p["params"]["top_n"] in carry.TOP_N and p["test"]["windows"] > 100
    assert len(r["coins"]) == 8 and all(isinstance(m["month"], str) for m in p["monthly_test"])
    assert p["equity"][0]["equity"] == pytest.approx(1000.0, abs=50)
    import json
    json.dumps(r)  # API'den döndürülebilir olmalı
