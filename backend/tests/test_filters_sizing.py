from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.exchange.filters import check_qty, floor_step, fmt, round_tick, stop_price, tp_price
from app.models import SymbolInfo
from app.risk.sizing import SizingInput, approx_liq_price, max_safe_leverage, size_and_leverage, symbol_class


def test_floor_step():
    assert floor_step(1.23456, "0.001") == Decimal("1.234")
    assert floor_step("10", "1") == Decimal("10")
    assert floor_step(0.0009, "0.001") == 0


def test_round_tick_modes():
    assert round_tick(100.07, "0.05", "down") == Decimal("100.05")
    assert round_tick(100.07, "0.05", "up") == Decimal("100.10")
    assert round_tick(100.07, "0.05", "nearest") == Decimal("100.05")


def test_fmt_has_no_exponent():
    assert fmt(Decimal("1E+2")) == "100"
    assert fmt(Decimal("0.00001000")) == "0.00001"


def test_stop_and_tp_round_toward_entry():
    assert stop_price("LONG", 99.987, "0.01") == Decimal("99.99")
    assert stop_price("SHORT", 100.013, "0.01") == Decimal("100.01")
    assert tp_price("LONG", 103.456, "0.01") == Decimal("103.45")
    assert tp_price("SHORT", 96.544, "0.01") == Decimal("96.55")


def test_check_qty():
    sym = SymbolInfo(symbol="X", min_qty="0.01", market_max_qty="100", min_notional="5")
    assert check_qty(sym, Decimal("0.001"), 100) is not None
    assert "minimum" in (check_qty(sym, Decimal("0.04"), 100) or "")
    assert check_qty(sym, Decimal("0.06"), 100) is None
    assert check_qty(sym, Decimal("0.04"), 100, reduce_only=True) is None
    assert check_qty(sym, Decimal("101"), 100) is not None


def base(**kw) -> SizingInput:
    d = dict(equity=1000.0, entry=100.0, stop=98.0, risk_pct=0.01, taker_fee=0.0005, slippage=0.0005,
             max_margin_pct=0.15, mmr=0.005, class_cap=10, bracket_cap=50, global_cap=20, liq_buffer_k=3.0)
    d.update(kw)
    return SizingInput(**d)


def test_guide_example_matches_document():
    r = size_and_leverage(base())
    assert r is not None
    assert round(r.notional, 1) == 465.1
    assert r.leverage == 4
    assert abs(r.risk_usdt - 10.0) < 1e-9
    assert r.lev_liq == 15


def test_leverage_reduced_when_cap_hit_shrinks_position():
    r = size_and_leverage(base(class_cap=2))
    assert r is not None and r.leverage == 2 and r.reduced
    assert r.notional <= 1000 * 0.15 * 2 + 1e-9


def test_override_respects_safety_cap():
    r = size_and_leverage(base(leverage_override=50))
    assert r is not None and r.leverage == r.lev_cap == 10


def test_symbol_class():
    assert symbol_class("BTCUSDT", 50) == "major"
    assert symbol_class("SOLUSDT", 5) == "large"
    assert symbol_class("XYZUSDT", 80) == "other"


@settings(max_examples=400, deadline=None)
@given(
    equity=st.floats(50, 1e6),
    entry=st.floats(0.0001, 1e5),
    dist=st.floats(0.002, 0.2),
    risk=st.floats(0.001, 0.02),
    is_long=st.booleans(),
    mmr=st.floats(0.002, 0.05),
    k=st.floats(1.5, 6.0),
    cap=st.integers(1, 125),
    override=st.one_of(st.none(), st.integers(1, 125)),
)
def test_sizing_invariants(equity, entry, dist, risk, is_long, mmr, k, cap, override):
    stop = entry * (1 - dist) if is_long else entry * (1 + dist)
    p = SizingInput(equity=equity, entry=entry, stop=stop, risk_pct=risk, taker_fee=0.0005, slippage=0.0005,
                    max_margin_pct=0.15, mmr=mmr, class_cap=cap, bracket_cap=125, global_cap=20, liq_buffer_k=k,
                    leverage_override=override)
    r = size_and_leverage(p)
    d = abs(entry - stop) / entry
    if r is None:
        assert max_safe_leverage(d, mmr, k) < 1
        return
    # işlem riski hedefi asla aşmaz
    assert r.risk_usdt <= equity * risk * (1 + 1e-9)
    # kaldıraç sınırlar içinde
    assert 1 <= r.leverage <= min(r.lev_liq, cap, 20)
    # marjin bütçesi aşılmaz
    assert r.margin <= equity * 0.15 * (1 + 1e-9)
    # likidasyon stop'tan en az k kat uzakta
    side = "LONG" if is_long else "SHORT"
    liq = approx_liq_price(side, entry, r.leverage, mmr)
    assert abs(entry - liq) / entry >= k * d - 1e-9
