"""Otomatik optimizasyon: en iyi strateji ayarlarını aşırı uyuma karşı korumalı biçimde bulur.

Yöntem:
1. Veri bir kez indirilir. Dönem ikiye bölünür: ilk ~2/3 "eğitim" (in-sample),
   son ~1/3 "doğrulama" (out-of-sample, ayar seçiminde HİÇ kullanılmaz).
2. Aday ayarlar (strateji seti, long/short izni, parametreler, beklenti eşiği) eğitim
   döneminde paralel olarak test edilir ve işlem R'lerinin t-istatistiğine göre sıralanır.
3. En iyi adaylar doğrulama döneminde test edilir. Seçim yalnızca doğrulama sonucuna göre
   yapılır; doğrulamada zarar eden aday asla önerilmez.
4. Seçilen ayar tüm dönemde çalıştırılır; kalibrasyon ve grafik bu çalıştırmadan gelir.
"""
from __future__ import annotations

import math
import os
import random
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from typing import Any, Callable, Optional

import numpy as np

from ..models import SymbolInfo
from ..settings import RiskSettings, ScannerSettings, StrategySettings
from . import metrics as M
from .data import BacktestCancelled, HistoryData
from .simulator import Backtester, BacktestConfig

STRATEGY_SETS: list[tuple[str, ...]] = [("S1",), ("S2",), ("S1", "S2"), ("S1", "S3"), ("S1", "S2", "S3")]
SPACE = {
    "s1_donchian": [36, 48, 72],
    "s1_vol_z": [1.5, 2.0, 2.5],
    "s1_atr_mult": [1.5, 2.0, 2.5, 3.0],
    "s2_top_n": [2, 3, 4],
    "s3_bb_k": [2.0, 2.5, 3.0],
    "s3_rsi": [20.0, 25.0, 30.0],
    "min_ev_r": [0.0, 0.1, 0.2],
}
SIDE_CHOICES = ["both", "long", "both", "long", "short"]  # short daha seyrek denenir
BUDGETS = {"hizli": 16, "normal": 32, "derin": 64}
TOP_K = 6

# --- işçi süreç durumu (her işçiye veri bir kez aktarılır) ----------------------------
_W: dict[str, Any] = {}


def _init_worker(data: HistoryData, infos: dict[str, SymbolInfo], base: dict) -> None:
    _W["data"] = data
    _W["infos"] = infos
    _W["base"] = base


def _make_config(params: dict, start: int, end: int, base: dict) -> BacktestConfig:
    strat = StrategySettings.model_validate({**base["strategies"], **params["strategies"]})
    risk = RiskSettings.model_validate({**base["risk"], "min_ev_r": params["min_ev_r"]})
    return BacktestConfig(
        symbols=base["symbols"], start_ms=start, end_ms=end, initial_equity=base["initial_equity"],
        s1=strat.s1_enabled, s2=strat.s2_enabled, s3=strat.s3_enabled,
        slippage_bps=base["slippage_bps"], taker_fee=base["taker_fee"], maker_fee=base["maker_fee"],
        risk=risk, strategies=strat, scanner=ScannerSettings.model_validate(base["scanner"]),
    )


def _evaluate(trades: list[dict], eq_v: np.ndarray, initial: float) -> dict:
    rs = [t["r"] for t in trades]
    st = M.trade_stats(rs, [t["pnl"] for t in trades])
    n = len(rs)
    sd = float(np.std(rs, ddof=1)) if n > 1 else 0.0
    t_stat = (float(np.mean(rs)) / sd * math.sqrt(n)) if n > 1 and sd > 0 else 0.0
    dd = float(-M.drawdown_series(eq_v).min()) if len(eq_v) else 0.0
    final = float(eq_v[-1]) if len(eq_v) else initial
    return {
        "trades": n, "win_rate": st["win_rate"], "profit_factor": st["profit_factor"],
        "expectancy_r": st["expectancy_r"], "t_stat": t_stat, "max_drawdown_pct": dd,
        "return_pct": (final / initial - 1.0) * 100.0 if initial > 0 else 0.0,
    }


def run_candidate(params: dict, start: int, end: int, full: bool = False) -> dict:
    """İşçi süreçte bir adayı çalıştırır."""
    base = _W["base"]
    cfg = _make_config(params, start, end, base)
    raw = Backtester(cfg, _W["data"], _W["infos"], lambda f, m: None, lambda: False).run()
    out = {"params": params, "eval": _evaluate(raw["trades"], raw["eq_v"], base["initial_equity"])}
    if full:
        out["raw"] = raw
    return out


# --- aday üretimi ----------------------------------------------------------------------
def params_from_settings(strategies: StrategySettings, risk: RiskSettings) -> dict:
    s = strategies
    return {
        "strategies": {
            "s1_enabled": s.s1_enabled, "s2_enabled": s.s2_enabled, "s3_enabled": s.s3_enabled,
            "s1_sides": s.s1_sides, "s2_sides": s.s2_sides, "s3_sides": s.s3_sides,
            "s1_donchian": s.s1_donchian, "s1_vol_z": s.s1_vol_z, "s1_atr_mult": s.s1_atr_mult,
            "s2_top_n": s.s2_top_n, "s3_bb_k": s.s3_bb_k, "s3_rsi": s.s3_rsi,
        },
        "min_ev_r": risk.min_ev_r,
    }


def generate_candidates(n: int, baseline: dict, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    out: list[dict] = [baseline]
    seen = {repr(baseline)}
    # yapılandırılmış başlangıç adayları: her strateji seti, iki yönlü ve sadece long
    for sset in STRATEGY_SETS:
        for sides in ("both", "long"):
            p = _build(sset, {k: v for k, v in baseline["strategies"].items()}, baseline["min_ev_r"], sides_all=sides)
            if repr(p) not in seen:
                seen.add(repr(p))
                out.append(p)
    guard = 0
    while len(out) < n and guard < n * 50:
        guard += 1
        sset = rng.choice(STRATEGY_SETS)
        strat = {k: rng.choice(v) for k, v in SPACE.items() if k != "min_ev_r"}
        p = _build(sset, strat, rng.choice(SPACE["min_ev_r"]), rng=rng)
        if repr(p) not in seen:
            seen.add(repr(p))
            out.append(p)
    return out[:n]


def _build(sset: tuple[str, ...], strat: dict, min_ev: float, sides_all: Optional[str] = None,
           rng: Optional[random.Random] = None) -> dict:
    s = {
        "s1_enabled": "S1" in sset, "s2_enabled": "S2" in sset, "s3_enabled": "S3" in sset,
        "s1_donchian": strat.get("s1_donchian", 48), "s1_vol_z": strat.get("s1_vol_z", 2.0),
        "s1_atr_mult": strat.get("s1_atr_mult", 2.0), "s2_top_n": strat.get("s2_top_n", 3),
        "s3_bb_k": strat.get("s3_bb_k", 2.5), "s3_rsi": strat.get("s3_rsi", 25.0),
    }
    for code in ("s1", "s2", "s3"):
        if sides_all is not None:
            s[f"{code}_sides"] = sides_all
        elif rng is not None:
            s[f"{code}_sides"] = rng.choice(SIDE_CHOICES)
        else:
            s[f"{code}_sides"] = "both"
    return {"strategies": s, "min_ev_r": float(min_ev)}


SIDE_TR = {"both": "long + short", "long": "sadece long", "short": "sadece short"}


def describe(params: dict) -> dict:
    s = params["strategies"]
    parts = []
    if s["s1_enabled"]:
        parts.append(f"S1 Trend Kırılımı ({SIDE_TR[s['s1_sides']]}; kanal {s['s1_donchian']} bar, hacim z ≥ {s['s1_vol_z']:g}, stop {s['s1_atr_mult']:g}×ATR)")
    if s["s2_enabled"]:
        parts.append(f"S2 Göreli Güç ({SIDE_TR[s['s2_sides']]}; ilk/son {s['s2_top_n']})")
    if s["s3_enabled"]:
        parts.append(f"S3 Ortalamaya Dönüş ({SIDE_TR[s['s3_sides']]}; BB k={s['s3_bb_k']:g}, RSI {s['s3_rsi']:g})")
    label = " + ".join(
        code for code, on in (("S1", s["s1_enabled"]), ("S2", s["s2_enabled"]), ("S3", s["s3_enabled"])) if on
    )
    return {"label": label, "details": parts, "min_ev_r": params["min_ev_r"]}


# --- ana optimizasyon ---------------------------------------------------------------------
def _score_is(ev: dict, min_trades: int) -> float:
    if ev["trades"] < min_trades:
        return -1e9
    penalty = max(0.0, ev["max_drawdown_pct"] - 25.0) * 0.2
    return ev["t_stat"] - penalty


def _verdict(oos: dict) -> str:
    pf = oos["profit_factor"] or 0.0
    exp = oos["expectancy_r"] or 0.0
    if oos["trades"] >= 15 and exp >= 0.10 and pf >= 1.3:
        return "recommended"
    if oos["trades"] >= 10 and exp > 0 and pf > 1.0:
        return "weak"
    return "none"


def optimize(
    data: HistoryData,
    infos: dict[str, SymbolInfo],
    base: dict,
    baseline: dict,
    start: int,
    end: int,
    budget: str,
    progress: Callable[[str, float, str], None],
    cancelled: Callable[[], bool],
) -> dict:
    split = start + int((end - start) * 2 / 3)
    candidates = generate_candidates(BUDGETS.get(budget, 32), baseline)
    days_is = (split - start) / 86_400_000
    min_trades = int(max(20, days_is / 4))
    workers = max(1, min(6, (os.cpu_count() or 2) - 1))
    progress("search", 0.0, f"{len(candidates)} aday ayar, {workers} paralel işçiyle test ediliyor")

    def run_many(jobs: list[tuple[dict, int, int, bool]], phase: str, lo: float, hi: float, label: str) -> list[dict]:
        results: list[dict] = []
        try:
            ex = ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(data, infos, base))
        except Exception:
            ex = None
        if ex is None:
            _init_worker(data, infos, base)
            for i, j in enumerate(jobs):
                if cancelled():
                    raise BacktestCancelled()
                results.append(run_candidate(*j))
                progress(phase, lo + (hi - lo) * (i + 1) / len(jobs), f"{label} {i + 1}/{len(jobs)}")
            return results
        try:
            pending: set[Future] = {ex.submit(run_candidate, *j) for j in jobs}
            done_n = 0
            while pending:
                if cancelled():
                    for f in pending:
                        f.cancel()
                    raise BacktestCancelled()
                done, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
                for f in done:
                    results.append(f.result())
                    done_n += 1
                    progress(phase, lo + (hi - lo) * done_n / len(jobs), f"{label} {done_n}/{len(jobs)}")
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        return results

    is_results = run_many([(c, start, split, False) for c in candidates], "search", 0.0, 70.0, "Eğitim dönemi testi")
    for r in is_results:
        r["score"] = _score_is(r["eval"], min_trades)
    is_results.sort(key=lambda r: r["score"], reverse=True)
    baseline_is = next((r for r in is_results if r["params"] == baseline), None)
    top = [r for r in is_results if r["score"] > -1e8][:TOP_K]
    if baseline_is is not None and baseline_is not in top:
        top.append(baseline_is)

    oos_results = run_many([(r["params"], split, end, False) for r in top], "validate", 70.0, 88.0, "Doğrulama testi")
    oos_by = {repr(r["params"]): r["eval"] for r in oos_results}
    board = []
    for r in top:
        oos = oos_by.get(repr(r["params"]))
        if oos is None:
            continue
        board.append({"params": r["params"], "is": r["eval"], "oos": oos, "is_score": r["score"]})
    # seçim yalnızca doğrulama sonucuna göre; zarar edenler elenir
    def oos_key(b: dict) -> float:
        o = b["oos"]
        if o["trades"] < 10 or (o["expectancy_r"] or 0) <= 0:
            return -1e9 + b["is_score"] * 1e-3
        return o["t_stat"] + 0.2 * b["is_score"]

    board.sort(key=oos_key, reverse=True)
    best = board[0] if board else None
    baseline_row = next((b for b in board if b["params"] == baseline), None)
    verdict = _verdict(best["oos"]) if best else "none"

    full = None
    if best is not None and verdict != "none":
        progress("final", 90.0, "Seçilen ayar tüm dönemde çalıştırılıyor")
        _init_worker(data, infos, base)
        full = run_candidate(best["params"], start, end, full=True)
    progress("final", 98.0, "Sonuç hazırlanıyor")
    return {
        "verdict": verdict, "best": best, "baseline": baseline_row, "board": board,
        "candidates_tested": len(candidates), "split": split, "min_trades": min_trades, "full": full,
    }
