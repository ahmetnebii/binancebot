"""Strateji Laboratuvarı: binlerce kuralı tarar, veri madenciliği yanılgısına karşı korumalı seçer.

Neden korumalı? 10.000 rastgele kural denendiğinde bazıları yalnızca şans eseri harika görünür.
Bu yüzden:
1. Dönem üçe bölünür: EĞİTİM (%60), DOĞRULAMA (%20), FİNAL SINAVI (%20).
   Final sınavı arama boyunca HİÇ görülmez; kazanan tek bir kez orada test edilir.
2. Eğitim puanı sağlamlık ister: işlem R'lerinin t-istatistiği − tutarlılık cezası (eğitim
   4 zaman dilimine bölünür, çoğu kârlı olmalı) − yaygınlık cezası (kâr tek bir coinden gelmemeli).
3. "Şans çıtası": N deneme yapıldığında saf şansla beklenen en yüksek t-istatistiği ≈ √(2·ln N).
   Kazananın eğitim t'si bunu geçmezse en fazla "zayıf" denir.
4. Hızlı tarama (vektörel, işlem başına R) adayları eler; finalistler canlıyla AYNI portföy
   simülatöründe (risk yöneticisi, pozisyon limitleri, komisyon, funding) yeniden test edilir.
"""
from __future__ import annotations

import json
import math
import os
import random
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from typing import Any, Callable, Optional

import numpy as np

from ..features.indicators import aggregate_bars
from ..models import SymbolInfo
from ..settings import RiskSettings, ScannerSettings, StrategySettings
from ..strategies.lab_rules import (
    GROUP_TR, ByteLRU, Ind, btc_trend_series, describe_rule, hourly_vol, mutate, random_rule, rule_signals, stop_for,
)
from ..util import TF_MS
from . import metrics as M
from .data import BacktestCancelled, HistoryData
from .simulator import Backtester, BacktestConfig

BUDGETS = {"hizli": 2000, "normal": 10000, "derin": 30000, "maksimum": 100000}
SHORTLIST = 200   # eğitimde en iyi bu kadar aday doğrulamaya geçer
FINALISTS = 8     # tam portföy simülasyonuna giren aday sayısı
CHUNK = 25        # işçiye tek seferde gönderilen aday sayısı
EVOLVE_SHARE = 0.3

_W: dict[str, Any] = {}


# --- işçi ---------------------------------------------------------------------------------
def _init_worker(data: HistoryData, infos: dict[str, SymbolInfo], base: dict) -> None:
    _W.clear()
    _W.update(data=data, infos=infos, base=base, tf={}, ind={}, btc={}, fund={},
              lru=ByteLRU(int(base.get("cache_mb", 500)) * 1024 * 1024))
    btc = data.bars.get("BTCUSDT")
    btc_1h = aggregate_bars(btc, TF_MS["1h"]) if btc is not None else None
    _W["btc_1h_t"] = (btc_1h["t"] + TF_MS["1h"]) if btc_1h is not None else None
    _W["btc_1h_tr"] = btc_trend_series(btc_1h) if btc_1h is not None else None
    for s, f in data.funding.items():
        if len(f[1]):
            _W["fund"][s] = float(np.mean(f[1]))


def _tf_arrays(sym: str, tf: str) -> dict[str, np.ndarray]:
    key = (sym, tf)
    if key not in _W["tf"]:
        a = _W["data"].bars[sym]
        _W["tf"][key] = a if tf == "15m" else aggregate_bars(a, TF_MS[tf])
    return _W["tf"][key]


def _ind(sym: str, tf: str) -> Ind:
    key = (sym, tf)
    if key not in _W["ind"]:
        _W["ind"][key] = Ind(_tf_arrays(sym, tf), cache=_W["lru"], prefix=key)
    return _W["ind"][key]


def _btc_for(sym: str, tf: str) -> Optional[np.ndarray]:
    key = (sym, tf)
    if key not in _W["btc"]:
        bt, tr = _W["btc_1h_t"], _W["btc_1h_tr"]
        if bt is None:
            _W["btc"][key] = None
        else:
            a = _tf_arrays(sym, tf)
            close_t = a["t"] + TF_MS[tf]
            idx = np.searchsorted(bt, close_t, side="right") - 1  # yalnızca kapanmış BTC mumları
            v = np.zeros(len(close_t))
            ok = idx >= 0
            v[ok] = tr[idx[ok]]
            _W["btc"][key] = v
    return _W["btc"][key]


def simulate_rule(rule: dict, start: int, end: int, symbols: Optional[list[str]] = None) -> list[tuple[int, str, float]]:
    """Hızlı, sembol başına tek pozisyonlu işlem simülasyonu. Dönüş: (giriş zamanı, sembol, R)."""
    base = _W["base"]
    taker, maker, slip = base["taker_fee"], base["maker_fee"], base["slippage_bps"] / 10_000.0
    tf = rule["tf"]
    step = TF_MS[tf]
    ex = rule["exit"]
    fixed = ex["mode"] == "fixed"
    tp_r = float(ex["tp_r"])
    trail_k = float(ex["trail_atr"] or 0.0)
    frac = 1.0 if fixed else 0.5
    time_bars = int(rule["time_bars"])
    k_stop = float(rule["stop_atr"])
    out: list[tuple[int, str, float]] = []
    for sym in symbols or base["symbols"]:
        if sym not in _W["data"].bars:
            continue
        a = _tf_arrays(sym, tf)
        n = len(a["t"])
        if n < 60:
            continue
        ind = _ind(sym, tf)
        lo, sh = rule_signals(ind, rule, _btc_for(sym, tf) if "btc" in rule["filters"] else None)
        sig_idx = np.flatnonzero(lo | sh)
        if len(sig_idx) == 0:
            continue
        t = a["t"]
        o, h, l, c = a["o"], a["h"], a["l"], ind.c  # noqa: E741
        at = ind.get(("atr",))
        fund = _W["fund"].get(sym, 0.0001)
        busy_until = -1
        for i in sig_idx:
            i = int(i)
            j0 = i + 1
            if j0 >= n or i <= busy_until:
                continue
            et = int(t[j0])
            if et < start or et >= end:
                continue
            sgn = 1 if lo[i] else -1
            stop = stop_for(float(c[i]), float(at[i]), sgn, k_stop)
            if stop is None:
                continue
            op = float(o[j0])
            if sgn * (op - stop) <= 0:
                continue  # açılış stop'u geçti
            entry = op * (1 + sgn * slip)
            risk = abs(entry - stop)
            if risk <= 0:
                continue
            tp = op + sgn * tp_r * abs(op - stop)
            pnl = -entry * taker
            rem = 1.0
            tp_done = False
            best = entry
            mfe = 0.0
            j = j0
            exit_j = n - 1
            while j < n:
                hj, lj = float(h[j]), float(l[j])
                if (sgn > 0 and lj <= stop) or (sgn < 0 and hj >= stop):
                    px = (min(stop, float(o[j])) if sgn > 0 else max(stop, float(o[j]))) * (1 - sgn * slip)
                    pnl += rem * (sgn * (px - entry) - px * taker)
                    rem = 0.0
                    exit_j = j
                    break
                if not tp_done and ((sgn > 0 and hj >= tp) or (sgn < 0 and lj <= tp)):
                    pnl += frac * (sgn * (tp - entry) - tp * maker)
                    rem -= frac
                    tp_done = True
                    if rem <= 1e-9:
                        exit_j = j
                        break
                    be = entry * (1 + sgn * 2 * taker)
                    if sgn * (be - stop) > 0:
                        stop = be
                ext = hj if sgn > 0 else lj
                if sgn * (ext - best) > 0:
                    best = ext
                mfe = max(mfe, sgn * (ext - entry) / risk)
                cj = float(c[j])
                aj = float(at[j])
                if tp_done and trail_k > 0 and aj > 0:
                    cand = best - sgn * trail_k * aj
                    if sgn * (cand - (stop + sgn * 0.25 * aj)) > 0 and sgn * ((cj - sgn * 0.1 * aj) - cand) > 0:
                        stop = cand
                if not tp_done and j - i >= time_bars and mfe < 0.5:
                    px = cj * (1 - sgn * slip)
                    pnl += rem * (sgn * (px - entry) - px * taker)
                    rem = 0.0
                    exit_j = j
                    break
                j += 1
            if rem > 0:
                px = float(c[n - 1])
                pnl += rem * (sgn * (px - entry) - px * taker)
            hours = (exit_j - j0 + 1) * step / 3_600_000
            pnl -= sgn * fund * entry * hours / 8.0
            busy_until = exit_j
            out.append((et, sym, pnl / risk))
    return out


def _stats(rs: list[float]) -> dict:
    n = len(rs)
    if n == 0:
        return {"trades": 0, "expectancy_r": None, "profit_factor": None, "win_rate": None, "t_stat": 0.0}
    arr = np.asarray(rs)
    gains = float(arr[arr > 0].sum())
    losses = float(-arr[arr < 0].sum())
    sd = float(arr.std(ddof=1)) if n > 1 else 0.0
    return {
        "trades": n, "expectancy_r": float(arr.mean()),
        "profit_factor": (gains / losses) if losses > 0 else (None if gains == 0 else 99.0),
        "win_rate": float((arr > 0).mean()),
        "t_stat": float(arr.mean() / sd * math.sqrt(n)) if sd > 0 and n > 1 else 0.0,
    }


def _robust(trades: list[tuple[int, str, float]], start: int, end: int, min_trades: int) -> dict:
    rs = [r for _, _, r in trades]
    st = _stats(rs)
    if st["trades"] < min_trades:
        st.update(score=-1e9, folds_pos=0, breadth=0.0)
        return st
    edges = np.linspace(start, end, 5)
    folds = [sum(r for et, _, r in trades if edges[k] <= et < edges[k + 1]) for k in range(4)]
    folds_pos = sum(1 for f in folds if f > 0)
    by_sym: dict[str, list[float]] = {}
    for _, s, r in trades:
        by_sym.setdefault(s, []).append(r)
    active = [v for v in by_sym.values() if len(v) >= 3]
    breadth = (sum(1 for v in active if sum(v) > 0) / len(active)) if active else 0.0
    penalty = (3 - min(3, folds_pos)) * 0.75 + max(0.0, 0.5 - breadth) * 4.0
    st.update(score=st["t_stat"] - penalty, folds_pos=folds_pos, breadth=breadth)
    return st


def evaluate_chunk(rules: list[dict], start: int, split1: int, split2: int, min_trades: int,
                   symbols: Optional[list[str]] = None) -> list[dict]:
    """İşçide: kuralları hızlı simüle eder, EĞİTİM ve DOĞRULAMA istatistiklerini döndürür.
    Final sınavı dönemi burada hesaplanmaz bile (seçime sızmasın)."""
    out = []
    for rule in rules:
        try:
            tr = simulate_rule(rule, start, split2, symbols)
        except Exception as exc:  # hatalı kural tüm aramayı durdurmasın
            out.append({"rule": rule, "is": {"trades": 0, "score": -1e9, "error": str(exc)}, "oos": None})
            continue
        is_tr = [x for x in tr if x[0] < split1]
        oos_tr = [x for x in tr if x[0] >= split1]
        out.append({"rule": rule, "is": _robust(is_tr, start, split1, min_trades), "oos": _stats([r for _, _, r in oos_tr])})
    return out


# --- tam portföy simülasyonu ---------------------------------------------------------------
def full_sim(rule: dict, start: int, end: int, keep_raw: bool = False, symbols: Optional[list[str]] = None) -> dict:
    base = _W["base"]
    strat = StrategySettings.model_validate({
        **base["strategies"], "s1_enabled": False, "s2_enabled": False, "s3_enabled": False,
        "s4_enabled": False, "s5_enabled": False, "lab_enabled": True, "lab_rule": rule,
    })
    cfg = BacktestConfig(
        symbols=symbols or base["symbols"], start_ms=start, end_ms=end, initial_equity=base["initial_equity"],
        s1=False, s2=False, s3=False, s4=False, s5=False, lab=True,
        slippage_bps=base["slippage_bps"], taker_fee=base["taker_fee"], maker_fee=base["maker_fee"],
        risk=RiskSettings.model_validate(base["risk"]), strategies=strat,
        scanner=ScannerSettings.model_validate(base["scanner"]),
    )
    raw = Backtester(cfg, _W["data"], _W["infos"], lambda f, m: None, lambda: False).run()
    rs = [t["r"] for t in raw["trades"]]
    st = _stats(rs)
    eq = raw["eq_v"]
    st["return_pct"] = (float(eq[-1]) / base["initial_equity"] - 1.0) * 100.0 if len(eq) else 0.0
    st["max_drawdown_pct"] = float(-M.drawdown_series(eq).min()) if len(eq) else 0.0
    out = {"rule": rule, "eval": st}
    if keep_raw:
        out["raw"] = raw
    return out


# --- orkestrasyon --------------------------------------------------------------------------
def rule_key(rule: dict) -> str:
    return json.dumps(rule, sort_keys=True)


def luck_bar(n_tested: int) -> float:
    """N bağımsız denemede saf şansla beklenen en yüksek t-istatistiği (≈ √(2·ln N))."""
    return math.sqrt(2.0 * math.log(max(2, n_tested)))


def verdict(final: dict, dev: dict, is_t: float, bar: float) -> str:
    """final: final sınavı (tam simülasyon), dev: eğitim+doğrulama (tam simülasyon)."""
    if (dev.get("expectancy_r") or 0.0) <= 0:
        return "none"
    if final.get("return_pct") is not None and final["return_pct"] <= 0:
        return "none"  # R cinsinden kâr görünse bile gerçek portföy parası azaldıysa avantaj yok
    pf = final.get("profit_factor") or 0.0
    exp = final.get("expectancy_r") or 0.0
    n = final.get("trades", 0)
    if n >= 15 and exp >= 0.10 and pf >= 1.3:
        return "recommended" if is_t >= bar else "weak"
    if n >= 15 and exp >= 0.05 and pf >= 1.1:
        return "weak"
    return "none"


def make_groups(data: HistoryData, symbols: list[str], start: int, split1: int) -> list[dict]:
    """Coinleri YALNIZCA eğitim dönemindeki davranışlarına göre gruplar (sonraki dönem bilgisi kullanılmaz).

    big : eğitimde en yüksek işlem hacimli coinler (≈ ilk %20, en az 4)
    mid : kalanlardan saatlik oynaklığı medyanın altında olanlar
    high: kalanlardan saatlik oynaklığı medyanın üstünde olanlar
    """
    stats = []
    for s in symbols:
        a = data.bars.get(s)
        if a is None:
            continue
        m = (a["t"] >= start) & (a["t"] < split1)
        if m.sum() < 500:
            continue
        h1 = aggregate_bars({k: v[m] for k, v in a.items()}, TF_MS["1h"])
        stats.append((s, float(a["qv"][m].sum()), hourly_vol(h1["c"])))
    stats.sort(key=lambda x: x[1], reverse=True)
    n_big = max(4, len(stats) // 5)
    big = [s for s, _, _ in stats[:n_big]]
    rest = sorted([x for x in stats[n_big:] if math.isfinite(x[2])], key=lambda x: x[2])
    if len(rest) < 8:
        return [{"key": "all", "symbols": [s for s, _, _ in stats], "vol_min": None, "vol_max": None}]
    mid_n = len(rest) // 2
    split_vol = (rest[mid_n - 1][2] + rest[mid_n][2]) / 2.0
    return [
        {"key": "big", "symbols": big, "vol_min": None, "vol_max": None},
        {"key": "mid", "symbols": [s for s, _, _ in rest[:mid_n]], "vol_min": None, "vol_max": split_vol},
        {"key": "high", "symbols": [s for s, _, _ in rest[mid_n:]], "vol_min": split_vol, "vol_max": None},
    ]


def forward_ok(best: Optional[dict]) -> bool:
    """Simülasyonda canlı denemeye değer mi: dört aşamanın hepsinde pozitif beklenti."""
    if not best or not best.get("final"):
        return False
    stages = [best["is"], best["oos"] or {}, best["dev"] or {}, best["final"]]
    return all((st.get("expectancy_r") or 0.0) > 0 for st in stages) and best["final"].get("trades", 0) >= 5


def run_lab(
    data: HistoryData,
    infos: dict[str, SymbolInfo],
    base: dict,
    start: int,
    end: int,
    budget: str,
    progress: Callable[[str, float, str], None],
    cancelled: Callable[[], bool],
    seed: int = 20261103,
    grouped: bool = False,
    mode: Optional[str] = None,
) -> dict:
    """mode: "single" (tek kural), "groups" (3 coin grubu), "coins" (her coine ayrı kural)."""
    mode = mode or ("groups" if grouped else "single")
    total = BUDGETS.get(budget, 5000)
    split1 = start + int((end - start) * 0.6)
    split2 = start + int((end - start) * 0.8)
    days = (end - start) / 86_400_000
    # uzun geçmişte her işçi daha çok bellek tutar: işçi sayısını ona göre sınırla
    cap_workers = 6 if days <= 400 else 4
    workers = max(1, min(cap_workers, (os.cpu_count() or 2) - 1))
    if mode == "coins":
        groups = [{"key": s, "symbols": [s], "vol_min": None, "vol_max": None} for s in base["symbols"] if s in data.bars]
    elif mode == "groups":
        groups = make_groups(data, base["symbols"], start, split1)
    else:
        groups = [{"key": "all", "symbols": [s for s in base["symbols"] if s in data.bars], "vol_min": None, "vol_max": None}]

    ex: Optional[ProcessPoolExecutor]
    try:
        ex = ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(data, infos, base))
    except Exception:
        ex = None
        _init_worker(data, infos, base)

    span = 100.0 / len(groups)
    try:
        outs = []
        for gi, g in enumerate(groups):
            name = GROUP_TR.get(g["key"], g["key"])
            lo = gi * span

            def gprog(phase: str, pct: float, msg: str, lo: float = lo, name: str = name, gi: int = gi) -> None:
                prefix = f"[{gi + 1}/{len(groups)} · {name}] " if len(groups) > 1 else ""
                progress(phase, lo + pct * span / 100.0, prefix + msg)

            res = _search_group(ex, data, infos, base, g["symbols"], start, split1, split2, end, total, gprog, cancelled,
                                seed + gi * 7919, workers)
            res.update(key=g["key"], name=name, symbols=g["symbols"], vol_min=g["vol_min"], vol_max=g["vol_max"])
            res["forward_ok"] = forward_ok(res["best"])
            outs.append(res)
        order = {"recommended": 2, "weak": 1, "none": 0}
        overall = max((o["verdict"] for o in outs), key=lambda v: order[v]) if outs else "none"
        return {"verdict": overall, "groups": outs, "split1": split1, "split2": split2, "workers": workers}
    finally:
        if ex is not None:
            ex.shutdown(wait=False, cancel_futures=True)


def _search_group(
    ex: Optional[ProcessPoolExecutor], data: HistoryData, infos: dict, base: dict, symbols: list[str],
    start: int, split1: int, split2: int, end: int, total: int,
    progress: Callable[[str, float, str], None], cancelled: Callable[[], bool], seed: int, workers: int,
) -> dict:
    days_is = (split1 - start) / 86_400_000
    # grup küçüldükçe gereken işlem sayısı da orantılı (ama en az 20)
    min_trades = int(max(20, days_is / 3 * min(1.0, len(symbols) / 30)))
    rng = random.Random(seed)
    seen: set[str] = set()

    def run_batch(rules: list[dict], lo: float, hi: float, label: str, done_before: int, grand_total: int) -> list[dict]:
        rules = sorted(rules, key=lambda r: r["tf"])  # aynı zaman dilimi aynı işçide: önbellek isabeti
        chunks = [rules[i:i + CHUNK] for i in range(0, len(rules), CHUNK)]
        results: list[dict] = []

        def report() -> None:
            d = done_before + len(results)
            progress("search", lo + (hi - lo) * len(results) / max(1, len(rules)), f"{label}: {d:,}/{grand_total:,} kural".replace(",", "."))

        if ex is None:
            for ch in chunks:
                if cancelled():
                    raise BacktestCancelled()
                results += evaluate_chunk(ch, start, split1, split2, min_trades, symbols)
                report()
            return results
        pending: set[Future] = {ex.submit(evaluate_chunk, ch, start, split1, split2, min_trades, symbols) for ch in chunks}
        while pending:
            if cancelled():
                for f in pending:
                    f.cancel()
                raise BacktestCancelled()
            done, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
            for f in done:
                results += f.result()
            if done:
                report()
        return results

    def fresh(make: Callable[[], dict], k: int) -> list[dict]:
        out: list[dict] = []
        guard = 0
        while len(out) < k and guard < k * 30:
            guard += 1
            r = make()
            key = rule_key(r)
            if key not in seen:
                seen.add(key)
                out.append(r)
        return out

    def call(fn: Callable, *args: Any) -> Any:
        if ex is None:
            return fn(*args)
        f = ex.submit(fn, *args)
        while not f.done():
            if cancelled():
                raise BacktestCancelled()
            wait([f], timeout=1.0)
        return f.result()

    n_rand = int(total * (1 - EVOLVE_SHARE))
    progress("search", 0.0, f"{total:,} kural hazırlanıyor".replace(",", ".") + f" ({len(symbols)} coin, {workers} paralel işçi)")
    first = fresh(lambda: random_rule(rng), n_rand)
    res = run_batch(first, 0.0, 60.0, "Keşif taraması", 0, total)
    res.sort(key=lambda r: r["is"]["score"], reverse=True)
    parents = [r["rule"] for r in res if r["is"]["score"] > -1e8][:40]
    if parents:
        kids = fresh(lambda: mutate(rng.choice(parents), rng), total - len(first))
        res += run_batch(kids, 60.0, 82.0, "Evrimsel iyileştirme", len(first), total)
    res.sort(key=lambda r: r["is"]["score"], reverse=True)
    tested = len(res)
    bar = luck_bar(tested)

    short = [r for r in res if r["is"]["score"] > -1e8][:SHORTLIST]
    passed = [r for r in short if (r["oos"] or {}).get("trades", 0) >= 10 and (r["oos"]["expectancy_r"] or 0) > 0]
    passed.sort(key=lambda r: r["oos"]["t_stat"] + 0.5 * r["is"]["score"], reverse=True)
    finalists = passed[:FINALISTS]
    progress("validate", 84.0, f"{len(short)} güçlü aday, {len(passed)} tanesi doğrulamada da kârlı")

    if ex is None:
        _init_worker(data, infos, base)
    dev_runs: list[dict] = []
    if finalists:
        futs = [ex.submit(full_sim, r["rule"], start, split2, False, symbols) for r in finalists] if ex is not None else None
        for k, r in enumerate(finalists):
            if futs is not None:
                f = futs[k]
                while not f.done():
                    if cancelled():
                        raise BacktestCancelled()
                    wait([f], timeout=1.0)
                dev_runs.append(f.result())
            else:
                dev_runs.append(full_sim(r["rule"], start, split2, False, symbols))
            progress("validate", 84.0 + 8.0 * (k + 1) / len(finalists), f"Gerçekçi portföy simülasyonu {k + 1}/{len(finalists)}")
    fast_by = {rule_key(r["rule"]): r for r in finalists}
    dev_runs.sort(key=lambda d: d["eval"]["t_stat"], reverse=True)
    winner = next((d for d in dev_runs if (d["eval"]["expectancy_r"] or 0) > 0), None)

    def row(r: dict) -> dict:
        return {**describe_rule(r["rule"]), "rule": r["rule"], "is": r["is"], "oos": r["oos"]}

    best = None
    full = None
    v = "none"
    if winner is not None:
        progress("final", 93.0, "Final sınavı: kazanan kural hiç görülmemiş son dönemde test ediliyor")
        final = call(full_sim, winner["rule"], split2, end, False, symbols)
        full = call(full_sim, winner["rule"], start, end, True, symbols)
        fast = fast_by[rule_key(winner["rule"])]
        v = verdict(final["eval"], winner["eval"], fast["is"]["t_stat"], bar)
        best = {**row(fast), "dev": winner["eval"], "final": final["eval"]}
    return {
        "verdict": v, "best": best, "tested": tested, "luck_bar": bar, "min_trades": min_trades,
        "shortlist": len(short), "passed_oos": len(passed),
        "finalists": [{**describe_rule(d["rule"]), "dev": d["eval"]} for d in dev_runs],
        "leaderboard": [row(r) for r in short[:15]],
        "full": full,
    }
