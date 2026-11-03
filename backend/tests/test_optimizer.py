"""Otomatik ayar bulucu: yön izni, aday üretimi, karar eşikleri, tekilleştirme."""
import asyncio

import pytest

from app.backtest.data import SyntheticHistory
from app.backtest.manager import _dedupe
from app.backtest.optimizer import _verdict, describe, generate_candidates, params_from_settings
from app.backtest.simulator import Backtester, BacktestConfig
from app.market.synthetic import SPECS
from app.settings import RiskSettings, ScannerSettings, StrategySettings
from app.strategies.base import side_allowed
from app.util import now_ms


def test_side_allowed():
    st = StrategySettings(s1_sides="long", s2_sides="short")
    assert side_allowed("S1", "LONG", st) and not side_allowed("S1", "SHORT", st)
    assert side_allowed("S2", "SHORT", st) and not side_allowed("S2", "LONG", st)
    assert side_allowed("S3", "LONG", st) and side_allowed("S3", "SHORT", st)  # varsayılan both
    assert side_allowed("XX", "LONG", st)  # bilinmeyen strateji engellenmez


def test_generate_candidates_unique_and_baseline_first():
    base = params_from_settings(StrategySettings(), RiskSettings())
    cands = generate_candidates(32, base)
    assert cands[0] == base and len(cands) == 32
    assert len({repr(c) for c in cands}) == 32
    for c in cands:
        s = c["strategies"]
        assert s["s1_enabled"] or s["s2_enabled"] or s["s3_enabled"]
        assert describe(c)["label"]


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
