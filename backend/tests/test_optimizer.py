"""Otomatik ayar bulucu: yön izni, aday üretimi, karar eşikleri, tekilleştirme."""
import asyncio

import pytest

from app.backtest.data import SyntheticHistory
from app.backtest.manager import _dedupe
from app.backtest.optimizer import _key, _verdict, describe, generate_candidates, params_from_settings, refine_candidates, structures
from app.backtest.simulator import Backtester, BacktestConfig
from app.market.synthetic import SPECS
from app.settings import RiskSettings, ScannerSettings, StrategySettings
from app.strategies.base import apply_exit_profile, make_signal, passes_filters, side_allowed
from app.util import now_ms


def test_side_allowed():
    st = StrategySettings(s1_sides="long", s2_sides="short")
    assert side_allowed("S1", "LONG", st) and not side_allowed("S1", "SHORT", st)
    assert side_allowed("S2", "SHORT", st) and not side_allowed("S2", "LONG", st)
    assert side_allowed("S3", "LONG", st) and side_allowed("S3", "SHORT", st)  # varsayılan both
    assert side_allowed("XX", "LONG", st)  # bilinmeyen strateji engellenmez


def test_generate_candidates_two_stage():
    base = params_from_settings(StrategySettings(), RiskSettings())
    cands = generate_candidates(32, base)
    assert cands[0] == base and len(cands) == 16
    keys = {_key(c) for c in cands}
    assert len(keys) == 16
    for c in cands:
        s = c["strategies"]
        assert set(s) == set(base["strategies"])  # aynı anahtar kümesi
        assert any(s[f"s{i}_enabled"] for i in range(1, 6))
        assert describe(c)["label"]
    assert len(structures()) == 52
    ref = refine_candidates(cands[1:4], 16, set(keys))
    assert len(ref) == 16 and not ({_key(r) for r in ref} & keys)
    for r in ref:  # yapı (hangi stratejiler açık) korunur
        on = tuple(r["strategies"][f"s{i}_enabled"] for i in range(1, 6))
        assert on in {tuple(c["strategies"][f"s{i}_enabled"] for i in range(1, 6)) for c in cands[1:4]}
        StrategySettings.model_validate(r["strategies"])  # geçerli ayar


def _sig(strategy="S1", side="LONG", entry=100.0, stop=98.0):
    return make_signal(
        symbol="ETHUSDT", side=side, strategy=strategy, strategy_name="x", timeframe="15m", bar_time=0, now=0,
        ttl_ms=1, entry=entry, stop=stop, tp_spec=[(1.5, 0.4), (3.0, 0.3)], atr_value=1.0, score=70,
        reasons=[], warnings=[], features={}, trailing_atr_mult=3.0, time_stop_bars=24,
    )


def test_passes_filters_regime_and_funding():
    normal = StrategySettings()
    strict = StrategySettings(regime_filter="strict")
    assert passes_filters(_sig(), normal, "RANGE", 0.0)
    assert not passes_filters(_sig(), strict, "RANGE", 0.0)
    assert passes_filters(_sig(), strict, "BULL", 0.0)
    assert not passes_filters(_sig(side="SHORT", stop=102.0), strict, "BULL", 0.0)
    assert passes_filters(_sig("S3"), strict, "RANGE", 0.0) and not passes_filters(_sig("S3"), strict, "BULL", 0.0)
    # kendi yönümüze funding ödüyorsak (long + pozitif funding) eşik üstünde girme
    assert not passes_filters(_sig(), normal, "BULL", 0.0008)
    assert passes_filters(_sig(side="SHORT", stop=102.0), normal, "BULL", 0.0008)
    assert passes_filters(_sig(), StrategySettings(funding_max=0.001), "BULL", 0.0008)
    assert not passes_filters(_sig(), StrategySettings(s1_sides="short"), "BULL", 0.0)


def test_exit_profiles():
    sig = apply_exit_profile(_sig(), StrategySettings(exit_profile="quick"))
    assert [t.r for t in sig.take_profits] == [1.0, 2.0] and sig.trailing_atr_mult is None
    assert sig.take_profits[0].price == pytest.approx(102.0)
    run = apply_exit_profile(_sig(), StrategySettings(exit_profile="runner"))
    assert [t.r for t in run.take_profits] == [2.0] and run.trailing_atr_mult == pytest.approx(3.75) and run.time_stop_bars == 48
    std = apply_exit_profile(_sig(), StrategySettings())
    assert [t.r for t in std.take_profits] == [1.5, 3.0]


def _ev(trades, exp, pf):
    return {"trades": trades, "expectancy_r": exp, "profit_factor": pf}


def test_verdict_thresholds():
    assert _verdict(_ev(20, 0.15, 1.5)) == "recommended"
    assert _verdict(_ev(14, 0.30, 2.0)) == "weak"  # az işlem → en fazla zayıf
    assert _verdict(_ev(20, 0.05, 1.1)) == "weak"
    assert _verdict(_ev(9, 0.5, 3.0)) == "none"
    assert _verdict(_ev(40, -0.02, 0.95)) == "none"
    assert _verdict(_ev(40, None, None)) == "none"


def test_dedupe_board():
    def row(t, e, r):
        ev = {"trades": t, "expectancy_r": e, "return_pct": r}
        return {"is": ev, "oos": ev}
    board = [row(10, 0.2, 5.0), row(10, 0.2, 5.0), row(12, 0.1, 2.0)]
    assert len(_dedupe(board)) == 2


@pytest.mark.parametrize("sides", ["long", "short"])
def test_simulator_respects_sides(sides):
    syms = [s[0] for s in SPECS][:8]
    end = now_ms()
    end -= end % 900_000
    start = end - 20 * 86_400_000
    data = asyncio.run(SyntheticHistory().load(syms, start - 12 * 86_400_000, end, lambda f, m: None, lambda: False))
    st = StrategySettings(s1_sides=sides, s2_sides=sides)
    cfg = BacktestConfig(symbols=syms, start_ms=start, end_ms=end, initial_equity=1000.0, s1=True, s2=True, s3=False,
                         slippage_bps=3.0, taker_fee=0.0005, maker_fee=0.0002, risk=RiskSettings(),
                         strategies=st, scanner=ScannerSettings())
    raw = Backtester(cfg, data, {}, lambda f, m: None, lambda: False).run()
    assert raw["trades"], "test verisi işlem üretmeli"
    assert {t["side"] for t in raw["trades"]} == {sides.upper()}


def test_s4_trend_follow_trades_in_backtest():
    syms = [s[0] for s in SPECS][:10]
    end = now_ms()
    end -= end % 900_000
    start = end - 40 * 86_400_000
    data = asyncio.run(SyntheticHistory().load(syms, start - 12 * 86_400_000, end, lambda f, m: None, lambda: False))
    st = StrategySettings(s1_enabled=False, s2_enabled=False, s4_enabled=True)
    cfg = BacktestConfig(symbols=syms, start_ms=start, end_ms=end, initial_equity=1000.0, s1=False, s2=False, s3=False, s4=True,
                         slippage_bps=3.0, taker_fee=0.0005, maker_fee=0.0002, risk=RiskSettings(),
                         strategies=st, scanner=ScannerSettings())
    raw = Backtester(cfg, data, {}, lambda f, m: None, lambda: False).run()
    assert raw["trades"] and {t["strategy"] for t in raw["trades"]} == {"S4"}
    # 4 saatlik kapanışlarda girilir: giriş zamanı (bir sonraki 15 dk açılışı) 4 saat sınırında
    assert all(t["entry_time"] % (4 * 3_600_000) == 0 for t in raw["trades"])
