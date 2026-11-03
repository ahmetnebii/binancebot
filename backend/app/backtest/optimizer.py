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

STRATEGY_SETS: list[tuple[str, ...]] = [
    ("S1",), ("S2",), ("S4",), ("S1", "S2"), ("S1", "S4"), ("S2", "S4"),
    ("S1", "S2", "S4"), ("S4", "S3"), ("S1", "S3"), ("S1", "S2", "S3"),
]
SPACE = {
    "s1_donchian": [36, 48, 72],
    "s1_vol_z": [1.5, 2.0, 2.5],
    "s1_atr_mult": [1.5, 2.0, 2.5, 3.0],
    "s2_top_n": [2, 3, 4],
    "s3_bb_k": [2.0, 2.5, 3.0],
    "s3_rsi": [20.0, 25.0, 30.0],
    "s4_donchian": [20, 30, 42],
    "s4_atr_mult": [2.0, 2.5, 3.0, 3.5],
    "funding_max": [0.0003, 0.0005, 0.001],
    "exit_profile": ["standard", "runner", "quick"],
    "min_ev_r": [0.0, 0.1, 0.2],
}
STRUCTURE_SIDES = ("both", "long")
STRUCTURE_REGIME = ("normal", "strict")
SIDE_CHOICES = ["both", "long", "both", "long", "short"]  # short daha seyrek denenir
BUDGETS = {"hizli": 24, "normal": 48, "derin": 96}
TOP_K = 8
REFINE_STRUCTURES = 3

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
        s1=strat.s1_enabled, s2=strat.s2_enabled, s3=strat.s3_enabled, s4=strat.s4_enabled,
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
    # tutarlılık: işlemler zamana göre 3 parçaya bölünür, kaç parça kârlı?
    ordered = [t["r"] for t in sorted(trades, key=lambda t: t["entry_time"])]
    chunks = [ordered[i * n // 3:(i + 1) * n // 3] for i in range(3)] if n >= 9 else []
    positive_parts = sum(1 for ch in chunks if ch and sum(ch) > 0)
    return {
        "trades": n, "win_rate": st["win_rate"], "profit_factor": st["profit_factor"],
        "positive_parts": positive_parts,
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
        "strategies": {k: getattr(s, k) for k in STRATEGY_KEYS},
        "min_ev_r": risk.min_ev_r,
    }


STRATEGY_KEYS = (
    "s1_enabled", "s2_enabled", "s3_enabled", "s4_enabled",
    "s1_sides", "s2_sides", "s3_sides", "s4_sides",
    "s1_donchian", "s1_vol_z", "s1_atr_mult", "s2_top_n", "s3_bb_k", "s3_rsi",
    "s4_donchian", "s4_atr_mult", "regime_filter", "funding_max", "exit_profile",
)


def _key(p: dict) -> str:
    return repr(sorted(p["strategies"].items())) + repr(p["min_ev_r"])


def structures() -> list[dict]:
    """1. aşama: yapı taraması (hangi stratejiler, hangi yön, hangi rejim kuralı) varsayılan parametrelerle."""
    out = []
    for regime in STRUCTURE_REGIME:
        for sides in STRUCTURE_SIDES:
            for sset in STRATEGY_SETS:
                out.append(_build(sset, {}, 0.1, sides_all=sides, regime=regime))
    return out


def generate_candidates(n: int, baseline: dict, seed: int = 7) -> list[dict]:
    """1. aşama adayları: mevcut ayar + yapı taraması (bütçenin yarısı)."""
    out: list[dict] = [baseline]
    seen = {_key(baseline)}
    for p in structures():
        if len(out) >= max(2, n // 2):
            break
        if _key(p) not in seen:
            seen.add(_key(p))
            out.append(p)
    return out


def refine_candidates(winners: list[dict], n: int, seen: set[str], seed: int = 11) -> list[dict]:
    """2. aşama: en iyi yapıların etrafında parametre inceltme (yapı sabit, parametreler rastgele)."""
    rng = random.Random(seed)
    out: list[dict] = []
    if not winners or n <= 0:
        return out
    guard = 0
    i = 0
    while len(out) < n and guard < n * 60:
        guard += 1
        w = winners[i % len(winners)]
        i += 1
        st = dict(w["strategies"])
        for k, choices in SPACE.items():
            if k == "min_ev_r":
                continue
            if rng.random() < 0.6:  # her parametreyi her seferinde değiştirme: komşuluk araması
                st[k] = rng.choice(choices)
        p = {"strategies": st, "min_ev_r": float(rng.choice(SPACE["min_ev_r"]) if rng.random() < 0.5 else w["min_ev_r"])}
        if _key(p) not in seen:
            seen.add(_key(p))
            out.append(p)
    return out


def _build(sset: tuple[str, ...], strat: dict, min_ev: float, sides_all: Optional[str] = None,
           rng: Optional[random.Random] = None, regime: str = "normal") -> dict:
    s = {
        "s1_enabled": "S1" in sset, "s2_enabled": "S2" in sset, "s3_enabled": "S3" in sset, "s4_enabled": "S4" in sset,
        "s1_donchian": strat.get("s1_donchian", 48), "s1_vol_z": strat.get("s1_vol_z", 2.0),
        "s1_atr_mult": strat.get("s1_atr_mult", 2.0), "s2_top_n": strat.get("s2_top_n", 3),
        "s3_bb_k": strat.get("s3_bb_k", 2.5), "s3_rsi": strat.get("s3_rsi", 25.0),
        "s4_donchian": strat.get("s4_donchian", 30), "s4_atr_mult": strat.get("s4_atr_mult", 2.5),
        "regime_filter": regime, "funding_max": strat.get("funding_max", 0.0005),
        "exit_profile": strat.get("exit_profile", "standard"),
    }
    for code in ("s1", "s2", "s3", "s4"):
        if sides_all is not None:
            s[f"{code}_sides"] = sides_all
        elif rng is not None:
            s[f"{code}_sides"] = rng.choice(SIDE_CHOICES)
        else:
            s[f"{code}_sides"] = "both"
    return {"strategies": s, "min_ev_r": float(min_ev)}


SIDE_TR = {"both": "long + short", "long": "sadece long", "short": "sadece short"}


EXIT_TR = {"standard": "standart hedefler", "runner": "kârı koştur (küçük kâr al + iz süren stop)", "quick": "hızlı kâr al (1R/2R)"}


def describe(params: dict) -> dict:
    s = {**StrategySettings().model_dump(), **params["strategies"]}
    parts = []
    if s["s1_enabled"]:
        parts.append(f"S1 Trend Kırılımı ({SIDE_TR[s['s1_sides']]}; kanal {s['s1_donchian']} bar, hacim z ≥ {s['s1_vol_z']:g}, stop {s['s1_atr_mult']:g}×ATR)")
    if s["s2_enabled"]:
        parts.append(f"S2 Göreli Güç ({SIDE_TR[s['s2_sides']]}; ilk/son {s['s2_top_n']})")
    if s["s3_enabled"]:
        parts.append(f"S3 Ortalamaya Dönüş ({SIDE_TR[s['s3_sides']]}; BB k={s['s3_bb_k']:g}, RSI {s['s3_rsi']:g})")
    if s["s4_enabled"]:
        parts.append(f"S4 4s Trend Takibi ({SIDE_TR[s['s4_sides']]}; kanal {s['s4_donchian']} mum, stop {s['s4_atr_mult']:g}×ATR)")
    parts.append("Rejim filtresi: " + ("sıkı (yalnızca piyasa yönünde)" if s["regime_filter"] == "strict" else "normal"))
    parts.append(f"Funding sınırı: %{s['funding_max'] * 100:.2f} · Çıkış: {EXIT_TR[s['exit_profile']]}")
    label = " + ".join(
        code for code, on in (("S1", s["s1_enabled"]), ("S2", s["s2_enabled"]), ("S3", s["s3_enabled"]), ("S4", s["s4_enabled"])) if on
    )
    if s["regime_filter"] == "strict":
        label += " · sıkı rejim"
    return {"label": label, "details": parts, "min_ev_r": params["min_ev_r"]}


# --- ana optimizasyon ---------------------------------------------------------------------
def _score_is(ev: dict, min_trades: int) -> float:
    if ev["trades"] < min_trades:
        return -1e9
    penalty = max(0.0, ev["max_drawdown_pct"] - 25.0) * 0.2
    # tek bir şanslı döneme dayanan sonucu cezalandır (3 parçanın en az 2'si kârlı olmalı)
    penalty += (2 - min(2, ev.get("positive_parts", 2))) * 0.75
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
    total_budget = BUDGETS.get(budget, 48)
    candidates = generate_candidates(total_budget, baseline)
    days_is = (split - start) / 86_400_000
    min_trades = int(max(20, days_is / 4))
    workers = max(1, min(6, (os.cpu_count() or 2) - 1))
    progress("search", 0.0, f"1. aşama: {len(candidates)} strateji yapısı, {workers} paralel işçiyle test ediliyor")

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

    is_results = run_many([(c, start, split, False) for c in candidates], "search", 0.0, 35.0, "Yapı taraması")
    for r in is_results:
        r["score"] = _score_is(r["eval"], min_trades)
    is_results.sort(key=lambda r: r["score"], reverse=True)
    # 2. aşama: en iyi yapıların parametrelerini incelt
    seen = {_key(r["params"]) for r in is_results}
    winners = [r["params"] for r in is_results if r["score"] > -1e8][:REFINE_STRUCTURES]
    refine = refine_candidates(winners, total_budget - len(candidates), seen)
    if refine:
        progress("search", 35.0, f"2. aşama: en iyi {len(winners)} yapı için {len(refine)} parametre varyasyonu")
        more = run_many([(c, start, split, False) for c in refine], "search", 35.0, 70.0, "Parametre inceltme")
        for r in more:
            r["score"] = _score_is(r["eval"], min_trades)
        is_results += more
        is_results.sort(key=lambda r: r["score"], reverse=True)
    candidates = candidates + refine
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
