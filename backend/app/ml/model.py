"""Kendi kendini eğiten çapraz kesit modeli (ridge regresyon) ve aylık yeniden eğitimli (walk-forward) test.

Her ayın başında model YALNIZCA o güne kadarki veriyle (hedefi bilinen günlerle, araya tampon konarak)
yeniden eğitilir ve o ay boyunca günlük tahmin yapar. Tahmini en yüksek N coin long, en düşük N coin short;
pozisyonlar H günde bir yenilenir. Sermaye: yarısı long, yarısı short (piyasa yönüne bahis yok).

Ayar seçimi (λ, N, H) yalnızca walk-forward döneminin İLK yarısına göre yapılır; raporlanan "final" sonuç
ikinci yarıdır (ayar seçiminde hiç kullanılmadı).
"""
from __future__ import annotations

import itertools
import math
from datetime import datetime, timezone
from typing import Callable, Optional

import numpy as np

from .features import DAY, FEATURES, Panel, forward_returns

LAMBDAS = [10.0, 100.0, 1000.0]
TOP_N = [3, 5, 10]
HOLD = [1, 3, 7]
MIN_TRAIN_DAYS = 300
EMBARGO = 2


def month_of(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m")


def ridge_fit(X: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    A = X.T @ X + lam * np.eye(X.shape[1])
    return np.linalg.solve(A, X.T @ y)


def _training_set(panel: Panel, fwd: np.ndarray, end_day: int) -> tuple[np.ndarray, np.ndarray]:
    """end_day'den ÖNCE hedefi tamamen bilinen gözlemler (piyasa ortalamasından arındırılmış hedef)."""
    Xs, ys = [], []
    for d in range(0, max(0, end_day)):
        ok = panel.tradable[d] & ~np.isnan(fwd[d]) & ~np.isnan(panel.X[d]).any(axis=1)
        if ok.sum() < 8:
            continue
        y = fwd[d, ok]
        Xs.append(panel.X[d, ok])
        ys.append(y - y.mean())
    if not Xs:
        return np.zeros((0, panel.X.shape[2])), np.zeros(0)
    return np.vstack(Xs), np.concatenate(ys)


def walk_forward_scores(panel: Panel, h: int, lam: float, progress: Optional[Callable[[float], None]] = None) -> tuple[np.ndarray, dict]:
    """Her gün için tahmin skorları [gün, coin] (NaN = tahmin yok) ve ay bazında katsayılar."""
    D = len(panel.t)
    fwd = forward_returns(panel.close, h)
    scores = np.full(panel.close.shape, np.nan)
    months = [month_of(int(x)) for x in panel.t]
    starts = [i for i in range(1, D) if months[i] != months[i - 1]]
    coefs = {}
    first = next((i for i in starts if i >= MIN_TRAIN_DAYS), None)
    if first is None:
        return scores, coefs
    blocks = [i for i in starts if i >= first] + [D]
    for bi in range(len(blocks) - 1):
        m0, m1 = blocks[bi], blocks[bi + 1]
        X, y = _training_set(panel, fwd, m0 - h - EMBARGO)
        if len(y) < 200:
            continue
        w = ridge_fit(X, y, lam)
        coefs[months[m0]] = w
        for d in range(m0, m1):
            ok = panel.tradable[d] & ~np.isnan(panel.X[d]).any(axis=1)
            if ok.sum() >= 8:
                scores[d, ok] = panel.X[d, ok] @ w
        if progress:
            progress((bi + 1) / (len(blocks) - 1))
    return scores, coefs


def simulate(panel: Panel, scores: np.ndarray, n: int, h: int, fee: float, slip: float) -> dict:
    """Günlük getiri serisi (sermayeye göre, 1x brüt). Her h günde bir yeniden dengeleme."""
    D, S = panel.close.shape
    r1 = forward_returns(panel.close, 1)
    rets = np.full(D, np.nan)
    long_set: set[int] = set()
    short_set: set[int] = set()
    trades = []
    entries = 0
    hold_left = 0
    cost_side = fee + slip
    for d in range(D - 1):
        if hold_left <= 0 and not np.isnan(scores[d]).all():
            sc = scores[d]
            ok = np.flatnonzero(~np.isnan(sc))
            if len(ok) >= 2 * n:
                order = ok[np.argsort(sc[ok])]
                new_long, new_short = set(order[-n:].tolist()), set(order[:n].tolist())
                turnover = (len(new_long ^ long_set) + len(new_short ^ short_set)) / (2 * n)
                for c in new_long - long_set:
                    trades.append({"symbol": panel.syms[c], "side": "LONG", "entry_time": int(panel.t[d] + DAY)})
                for c in new_short - short_set:
                    trades.append({"symbol": panel.syms[c], "side": "SHORT", "entry_time": int(panel.t[d] + DAY)})
                entries += len(new_long - long_set) + len(new_short - short_set)
                long_set, short_set = new_long, new_short
                hold_left = h
                cost = turnover * 2 * cost_side  # her değişen pozisyon: kapat + aç
            else:
                cost = 0.0
        else:
            cost = 0.0
        if long_set or short_set:
            lv = [r1[d, c] for c in long_set if not np.isnan(r1[d, c])]
            sv = [r1[d, c] for c in short_set if not np.isnan(r1[d, c])]
            if lv and sv:
                rets[d] = 0.5 * float(np.mean(lv)) - 0.5 * float(np.mean(sv)) - cost
            hold_left -= 1
    return {"rets": rets, "entries": entries, "trades": trades}


def metrics(rets: np.ndarray, t: np.ndarray, d0: int, d1: int) -> dict:
    seg = rets[d0:d1]
    ts = t[d0:d1]
    ok = ~np.isnan(seg)
    seg, ts = seg[ok], ts[ok]
    if len(seg) < 20:
        return {"days": int(len(seg)), "return_pct": 0.0, "apr_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe": 0.0, "t_stat": 0.0,
                "positive_months_pct": None, "monthly": [], "hit_pct": None}
    eq = np.cumprod(1 + seg)
    peak = np.maximum.accumulate(eq)
    dd = float(np.max(1 - eq / peak)) * 100.0
    months: dict[str, float] = {}
    for x, r in zip(ts, seg):
        k = month_of(int(x))
        months[k] = (1 + months.get(k, 0.0)) * (1 + r) - 1
    total = float(eq[-1] - 1) * 100.0
    years = len(seg) / 365.0
    return {
        "days": int(len(seg)),
        "return_pct": total,
        "apr_pct": ((1 + total / 100.0) ** (1 / years) - 1) * 100.0 if years > 0 and total > -100 else -100.0,
        "max_drawdown_pct": dd,
        "sharpe": float(seg.mean() / seg.std() * math.sqrt(365)) if seg.std() > 0 else 0.0,
        "t_stat": float(seg.mean() / seg.std() * math.sqrt(len(seg))) if seg.std() > 0 else 0.0,
        "positive_months_pct": sum(1 for v in months.values() if v > 0) / len(months) * 100.0 if months else None,
        "monthly": [{"month": k, "return_pct": v * 100.0} for k, v in sorted(months.items())],
        "hit_pct": float((seg > 0).mean()) * 100.0,
    }


def run(panel: Panel, fee: float = 0.0005, slip: float = 0.0005,
        progress: Optional[Callable[[float, str], None]] = None, cancelled: Callable[[], bool] = lambda: False) -> dict:
    D = len(panel.t)
    if D < MIN_TRAIN_DAYS + 120:
        raise ValueError(f"Model için en az {MIN_TRAIN_DAYS + 120} günlük veri gerekli (şu an {D})")
    grid = list(itertools.product(LAMBDAS, HOLD))
    score_cache: dict[tuple[float, int], np.ndarray] = {}
    coef_cache: dict[tuple[float, int], dict] = {}
    for gi, (lam, h) in enumerate(grid):
        if cancelled():
            from ..backtest.data import BacktestCancelled
            raise BacktestCancelled()
        sc, co = walk_forward_scores(panel, h, lam, (lambda f, gi=gi: progress(0.85 * (gi + f) / len(grid), "Model aylık olarak yeniden eğitiliyor ve test ediliyor")) if progress else None)
        score_cache[(lam, h)] = sc
        coef_cache[(lam, h)] = co
    # walk-forward dönemi: ilk tahmin gününden sona
    any_sc = next(iter(score_cache.values()))
    has = np.flatnonzero(~np.isnan(any_sc).all(axis=1))
    if len(has) < 60:
        raise ValueError("Walk-forward için yeterli tahmin günü oluşmadı")
    wf0, wf1 = int(has[0]), int(has[-1]) + 1
    mid = wf0 + (wf1 - wf0) // 2
    results = []
    for (lam, h), sc in score_cache.items():
        for n in TOP_N:
            sim = simulate(panel, sc, n, h, fee, slip)
            sel = metrics(sim["rets"], panel.t, wf0, mid)
            results.append({"lam": lam, "h": h, "n": n, "sel": sel, "sim": sim})
    results.sort(key=lambda r: (r["sel"]["sharpe"]), reverse=True)
    best = results[0]
    final = metrics(best["sim"]["rets"], panel.t, mid, wf1)
    full_wf = metrics(best["sim"]["rets"], panel.t, wf0, wf1)
    trades_final = [tr for tr in best["sim"]["trades"] if tr["entry_time"] >= int(panel.t[mid])]
    coefs = coef_cache[(best["lam"], best["h"])]
    W = np.array(list(coefs.values())) if coefs else np.zeros((1, len(FEATURES)))
    importance = sorted(
        [{"feature": FEATURES[i][0], "name": FEATURES[i][1], "coef": float(W[:, i].mean()),
          "stability_pct": float((np.sign(W[:, i]) == np.sign(W[:, i].mean())).mean() * 100.0)} for i in range(len(FEATURES))],
        key=lambda x: -abs(x["coef"]),
    )
    # son model: tüm veriyle eğit (canlı kullanım için)
    fwd = forward_returns(panel.close, best["h"])
    X, y = _training_set(panel, fwd, D - best["h"] - 1)
    w_last = ridge_fit(X, y, best["lam"]).tolist() if len(y) >= 200 else W[-1].tolist()
    # bugünün seçimi
    d = D - 1
    ok = panel.tradable[d] & ~np.isnan(panel.X[d]).any(axis=1)
    today = []
    if ok.sum() >= 2 * best["n"]:
        sc = panel.X[d, ok] @ np.array(w_last)
        idx = np.flatnonzero(ok)
        order = idx[np.argsort(sc)]
        today = ([{"symbol": panel.syms[c], "side": "LONG"} for c in order[-best["n"]:][::-1]]
                 + [{"symbol": panel.syms[c], "side": "SHORT"} for c in order[:best["n"]]])
    f = final
    # Uzun/kısa coin portföyü çok oynak: birkaç aylık sonuçta yüksek getiri şansla da görülebilir.
    # Bu yüzden getiri yanında istatistiksel anlamlılık (günlük getirilerin t-değeri) da aranır.
    if (f["days"] >= 120 and f["t_stat"] >= 2.5 and f["apr_pct"] >= 10 and f["max_drawdown_pct"] <= 25
            and (f["positive_months_pct"] or 0) >= 60):
        verdict = "recommended"
    elif f["days"] >= 90 and f["t_stat"] >= 1.8 and f["apr_pct"] > 3:
        verdict = "weak"
    else:
        verdict = "none"
    if progress:
        progress(1.0, "Model hazır")
    return {
        "verdict": verdict,
        "params": {"lambda": best["lam"], "hold_days": best["h"], "top_n": best["n"]},
        "period": {"start": int(panel.t[wf0]), "split": int(panel.t[mid]), "end": int(panel.t[wf1 - 1] + DAY)},
        "selection": {k: v for k, v in best["sel"].items() if k != "monthly"},
        "final": f,
        "walk_forward": {k: v for k, v in full_wf.items() if k != "monthly"},
        "trades_final": trades_final[-2000:],
        "importance": importance,
        "model": {"coef": w_last, "features": [x[0] for x in FEATURES], "lambda": best["lam"],
                  "hold_days": best["h"], "top_n": best["n"], "trained_until": int(panel.t[-1])},
        "today": today,
        "coins": len(panel.syms),
        "grid": [{"lambda": r["lam"], "hold_days": r["h"], "top_n": r["n"], "sel_sharpe": r["sel"]["sharpe"],
                  "sel_apr_pct": r["sel"]["apr_pct"]} for r in results[:15]],
    }
