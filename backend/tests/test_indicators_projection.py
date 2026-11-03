import math

import numpy as np

from app.engine.projection import _hit_upper_prob, build_projection
from app.features.indicators import adx, atr, beta, bollinger, correlation, ema, rsi, zscore_last
from app.models import Position, TakeProfit


def test_ema_seed_and_recursion():
    x = np.arange(1, 51, dtype=float)
    e = ema(x, 10)
    assert np.isnan(e[8])
    assert e[9] == 5.5
    alpha = 2 / 11
    assert math.isclose(e[10], alpha * 11 + (1 - alpha) * 5.5)


def test_atr_constant_range():
    n = 50
    h, l, c = np.full(n, 11.0), np.full(n, 9.0), np.full(n, 10.0)  # noqa: E741
    assert math.isclose(atr(h, l, c, 14)[-1], 2.0)


def test_rsi_bounds_and_trend():
    rng = np.random.default_rng(1)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 500)))
    r = rsi(c, 14)
    valid = r[~np.isnan(r)]
    assert (valid >= 0).all() and (valid <= 100).all()
    up = np.arange(1, 100, dtype=float)
    assert rsi(up, 14)[-1] == 100.0


def test_adx_high_in_strong_trend():
    t = np.arange(300, dtype=float)
    c = 100 + t
    h, l = c + 0.5, c - 0.5  # noqa: E741
    assert adx(h, l, c, 14)[-1] > 50


def test_bollinger_and_zscore():
    c = np.full(60, 10.0)
    mid, up, lo, w = bollinger(c, 20, 2.0)
    assert mid[-1] == 10.0 and w[-1] == 0.0
    x = np.r_[np.random.default_rng(2).normal(0, 1, 100), 10.0]
    assert zscore_last(x, 96) > 5


def test_correlation_and_beta():
    rng = np.random.default_rng(3)
    b = rng.normal(0, 0.01, 200)
    a = 2 * b
    assert math.isclose(beta(a, b), 2.0, rel_tol=1e-9)
    assert correlation(a, b) > 0.999


def test_hit_probability_formulas():
    assert math.isclose(_hit_upper_prob(0.3, 0.0, 1.0, 0.0, 0.01), 0.3)
    assert _hit_upper_prob(0.3, 0.0, 1.0, 0.001, 0.01) > 0.3
    assert _hit_upper_prob(0.3, 0.0, 1.0, -0.001, 0.01) < 0.3


def _pos(side: str, entry: float, stop: float, tp: float) -> Position:
    return Position(
        id="p", env="paper", symbol="BTCUSDT", side=side, strategy="S1", strategy_name="x", opened_at=0,
        entry_price=entry, qty=1, initial_qty=1, leverage=5, margin=20, notional=100, stop_price=stop,
        initial_stop=stop, take_profits=[TakeProfit(r=1.5, price=tp, fraction=0.5)], risk_usdt=abs(entry - stop),
    )


def test_projection_long_and_short():
    rng = np.random.default_rng(4)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.003, 200)))
    t = (np.arange(200) * 900_000).astype(np.int64)
    arrays = {"c": c, "t": t}
    mark = float(c[-1])
    long_p = build_projection(_pos("LONG", mark, mark * 0.98, mark * 1.03), arrays, "15m", mark)
    assert long_p is not None
    assert len(long_p["cone"]) == 33
    k = long_p["cone"][10]
    assert k["up2"] > k["up1"] > mark > k["dn1"] > k["dn2"]
    expected = (math.log(mark) - math.log(mark * 0.98)) / (math.log(mark * 1.03) - math.log(mark * 0.98))
    assert math.isclose(long_p["p_target_first"], round(expected, 4), abs_tol=1e-4)
    short_p = build_projection(_pos("SHORT", mark, mark * 1.02, mark * 0.97), arrays, "15m", mark)
    expected_s = (math.log(mark * 1.02) - math.log(mark)) / (math.log(mark * 1.02) - math.log(mark * 0.97))
    assert math.isclose(short_p["p_target_first"], round(expected_s, 4), abs_tol=1e-4)
    assert short_p["expected_bars"] > 0
