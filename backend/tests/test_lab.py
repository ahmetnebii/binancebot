"""Strateji Laboratuvarı: kural dili, nedensellik, canlı/hızlı tutarlılık, uçtan uca arama."""
import asyncio
import random

import numpy as np
import pytest

from app.backtest import lab
from app.backtest.data import SyntheticHistory
from app.features.regime import Regime
from app.market.synthetic import SPECS
from app.settings import RiskSettings, ScannerSettings, StrategySettings
from app.strategies.base import StrategyContext
from app.strategies.lab_rules import ENTRY_SPACE, Ind, LabRule, describe_rule, mutate, random_rule, rule_signals
from app.util import now_ms


@pytest.fixture(scope="module")
def hist():
    syms = [s[0] for s in SPECS][:8]
    end = now_ms()
    end -= end % 900_000
    start = end - 100 * 86_400_000
    data = asyncio.run(SyntheticHistory().load(syms, start - 12 * 86_400_000, end, lambda f, m: None, lambda: False))
    return syms, start, end, data


def test_rules_valid_and_described():
    rng = random.Random(3)
    seen_types = set()
    for _ in range(400):
        r = random_rule(rng)
        seen_types.add(r["entry"]["type"])
        assert r["tf"] in ("15m", "1h", "4h") and len(r["filters"]) <= 2
        assert not ({"adx_trend", "adx_range"} <= set(r["filters"]))
        m = mutate(r, rng)
        assert m["tf"] == r["tf"] and len(m["filters"]) <= 2
        d = describe_rule(m)
        assert d["label"] and len(d["details"]) == 6
    assert seen_types == set(ENTRY_SPACE)


def test_signals_are_causal(hist):
    """Geleceğe ait mumlar eklemek geçmiş sinyalleri değiştirmemeli (gelecek bilgisi yok)."""
    _, _, _, data = hist
    a = data.bars["ETHUSDT"]
    rng = random.Random(5)
    for _ in range(60):
        r = random_rule(rng)
        r["tf"] = "15m"
        r["filters"] = [f for f in r["filters"] if f != "btc"]
        cut = len(a["t"]) - 300
        part = {k: v[:cut] for k, v in a.items()}
        lo1, sh1 = rule_signals(Ind(part), r)
        lo2, sh2 = rule_signals(Ind(a), r)
        assert np.array_equal(lo1, lo2[:cut]) and np.array_equal(sh1, sh2[:cut]), r


class _View:
    def __init__(self, bufs):
        self.bufs = bufs
        self.universe = []

    def buffer(self, s, tf):
        return self.bufs.get((s, tf))

    def funding(self, s):
        return None


class _Buf:
    def __init__(self, a):
        self.a = a

    def __len__(self):
        return len(self.a["t"])

    def arrays(self):
        return self.a


def test_live_strategy_matches_fast_signal(hist):
    """Canlı LabRule, son mumda hızlı taramayla aynı sinyali üretmeli."""
    _, _, _, data = hist
    a = data.bars["SOLUSDT"]
    rng = random.Random(11)
    checked = 0
    for _ in range(300):
        r = random_rule(rng)
        r["tf"] = "15m"
        r["filters"] = [f for f in r["filters"] if f != "btc"]
        lo, sh = rule_signals(Ind(a), r)
        idx = np.flatnonzero(lo | sh)
        idx = idx[idx > 400]
        if len(idx) == 0:
            continue
        i = int(idx[-1])
        win = {k: v[i - 249 : i + 1] for k, v in a.items()}  # canlı tampon: son 250 mum
        ctx = StrategyContext(_View({("SOLUSDT", "15m"): _Buf(win)}), Regime(name="BULL"),
                              StrategySettings(lab_enabled=True, lab_rule=r), ScannerSettings(), int(win["t"][-1]))
        sig = LabRule().evaluate("SOLUSDT", ctx)
        lw, sw = rule_signals(Ind(win), r)
        assert (sig is not None) == bool(lw[-1] or sw[-1])
        if r["entry"]["type"] in ("donchian", "rsi_revert", "bb_revert", "bb_break", "volume_break", "roc") and not r["filters"]:
            # EMA tabanlı olmayan girişlerde pencere ile tam geçmiş birebir aynı sonucu verir
            assert sig is not None and sig.side == ("LONG" if lo[i] else "SHORT")
            checked += 1
    assert checked >= 3


def test_luck_bar_and_verdict():
    assert lab.luck_bar(10_000) == pytest.approx(4.29, abs=0.01)
    good = {"trades": 40, "expectancy_r": 0.2, "profit_factor": 1.6}
    dev_ok = {"expectancy_r": 0.1}
    assert lab.verdict(good, dev_ok, is_t=5.0, bar=4.3) == "recommended"
    assert lab.verdict(good, dev_ok, is_t=3.0, bar=4.3) == "weak"  # şans çıtasının altında
    assert lab.verdict(good, {"expectancy_r": -0.01}, is_t=9.0, bar=4.3) == "none"
    assert lab.verdict({"trades": 40, "expectancy_r": 0.01, "profit_factor": 1.02}, dev_ok, 9.0, 4.3) == "none"


def test_run_lab_end_to_end(hist, monkeypatch):
    syms, start, end, data = hist
    monkeypatch.setitem(lab.BUDGETS, "test", 80)
    base = {"symbols": syms, "initial_equity": 1000.0, "slippage_bps": 3.0, "taker_fee": 0.0005, "maker_fee": 0.0002,
            "strategies": StrategySettings().model_dump(), "risk": RiskSettings().model_dump(),
            "scanner": ScannerSettings().model_dump()}
    msgs = []
    out = lab.run_lab(data, {}, base, start, end, "test", lambda p, pct, m: msgs.append(pct), lambda: False)
    assert out["tested"] == 80 and out["verdict"] in ("recommended", "weak", "none")
    assert out["split1"] < out["split2"] < end and msgs == sorted(msgs)
    if out["best"]:
        b = out["best"]
        assert b["final"] is not None and b["dev"]["trades"] > 0 and out["full"] is not None
