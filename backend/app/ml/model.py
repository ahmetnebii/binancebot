"""Kendi kendini eğiten çapraz kesit modeli: aylık yeniden eğitim (walk-forward), iki model türü yarışır.

Modeller:
- ridge: doğrusal regresyon (sağlam, az ezberler)
- gbm  : LightGBM gradyan artırmalı karar ağaçları (özellikler arası etkileşimleri öğrenir; sıkı düzenlileştirme)

Hedef: riske göre getiri — sonraki H günün getirisi / (coinin oynaklığı × √H), aşırı değerler kırpılır ve o
günkü coin ortalamasından arındırılır. Böylece model "en çok sıçrayacak" oynak coinleri değil,
"riskine göre en iyi gidecek" coinleri öğrenir.

Portföy: tahmini en yüksek N coin long, en düşük N coin short; her bacak sermayenin yarısı, coinler
oynaklıklarıyla ters orantılı ağırlıklı (tek coin eşit ağırlığın en fazla 2 katı). Portföy oynaklığı
hedeflenir: stratejinin son 30 günlük gerçekleşen oynaklığı yüksekse pozisyonlar küçülür.

Ayar seçimi (model türü, H, N) walk-forward döneminin İLK yarısına göre yapılır; "final" ikinci yarıdır.
"""
from __future__ import annotations

import itertools
import math
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import numpy as np

from .features import DAY, FEATURES, Panel, forward_returns

try:  # LightGBM yoksa (ör. Windows'ta eksik sistem kütüphanesi) yalnızca ridge kullanılır
    import lightgbm as lgb
except Exception:  # pragma: no cover
    lgb = None

MODELS = ["ridge30", "ridge300", "gbm"]
HOLD = [1, 3, 7]
TOP_N = [8, 12]
MIN_TRAIN_DAYS = 300
EMBARGO = 2
TARGET_ANNUAL_VOL = 0.25
TARGET_CLIP = 4.0
GBM_PARAMS = {
    "objective": "regression", "learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 300,
    "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 10.0,
    "verbose": -1, "seed": 7, "deterministic": True, "num_threads": 4,
}
GBM_ROUNDS = 150


def month_of(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m")


def available_models() -> list[str]:
    return [m for m in MODELS if m != "gbm" or lgb is not None]


def ridge_fit(X: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    A = X.T @ X + lam * np.eye(X.shape[1])
    return np.linalg.solve(A, X.T @ y)


def _target(panel: Panel, fwd: np.ndarray, d: int, ok: np.ndarray, h: int) -> np.ndarray:
    v = panel.vol[d, ok] * math.sqrt(h)
    y = np.clip(fwd[d, ok] / np.maximum(v, 1e-4), -TARGET_CLIP, TARGET_CLIP)
    return y - y.mean()


def _training_set(panel: Panel, fwd: np.ndarray, end_day: int, h: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """end_day'den ÖNCE hedefi tamamen bilinen gözlemler (riske göre, piyasa-nötr hedef)."""
    Xs, ys = [], []
    for d in range(0, max(0, end_day)):
        ok = panel.tradable[d] & ~np.isnan(fwd[d])
        if ok.sum() < 8:
            continue
        Xs.append(panel.X[d, ok])
        ys.append(_target(panel, fwd, d, ok, h))
    if not Xs:
        return np.zeros((0, panel.X.shape[2])), np.zeros(0)
    return np.vstack(Xs), np.concatenate(ys)


def fit(kind: str, X: np.ndarray, y: np.ndarray) -> dict:
    """Model sözlüğü: canlıda da aynen kullanılır (kv'de saklanabilir)."""
    if kind.startswith("ridge"):
        lam = float(kind.replace("ridge", "") or 100)
        return {"type": "ridge", "coef": ridge_fit(X, y, lam).tolist(), "lambda": lam}
    if lgb is None:
        raise RuntimeError("LightGBM kurulu değil")
    booster = lgb.train(GBM_PARAMS, lgb.Dataset(X, y, free_raw_data=True), num_boost_round=GBM_ROUNDS)
    return {"type": "gbm", "booster": booster.model_to_string(), "_obj": booster,
            "gain": booster.feature_importance(importance_type="gain").tolist()}


def predict(model: dict, X: np.ndarray) -> np.ndarray:
    if model["type"] == "ridge":
        return X @ np.asarray(model["coef"])
    obj = model.get("_obj")
    if obj is None:
        obj = lgb.Booster(model_str=model["booster"])
        model["_obj"] = obj
    return obj.predict(X)


def walk_forward_scores(panel: Panel, h: int, kind: str, progress: Optional[Callable[[float], None]] = None) -> tuple[np.ndarray, list[dict]]:
    """Her gün için tahmin skorları [gün, coin] (NaN = tahmin yok) ve aylık modeller."""
    D = len(panel.t)
    fwd = forward_returns(panel.close, h)
    scores = np.full(panel.close.shape, np.nan)
    months = [month_of(int(x)) for x in panel.t]
    starts = [i for i in range(1, D) if months[i] != months[i - 1]]
    models: list[dict] = []
    first = next((i for i in starts if i >= MIN_TRAIN_DAYS), None)
    if first is None:
        return scores, models
    blocks = [i for i in starts if i >= first] + [D]
    for bi in range(len(blocks) - 1):
        m0, m1 = blocks[bi], blocks[bi + 1]
        X, y = _training_set(panel, fwd, m0 - h - EMBARGO, h)
        if len(y) < 300:
            continue
        mdl = fit(kind, X, y)
        models.append({k: v for k, v in mdl.items() if k not in ("_obj", "booster")})
        for d in range(m0, m1):
            ok = panel.tradable[d]
            if ok.sum() >= 8:
                scores[d, ok] = predict(mdl, panel.X[d, ok])
        if progress:
            progress((bi + 1) / (len(blocks) - 1))
    return scores, models


def leg_weights(vols: np.ndarray, idx: np.ndarray) -> dict[int, float]:
    """Bir bacak (long ya da short) için oynaklığa ters orantılı ağırlıklar; toplam 0.5, tek coin ≤ 2× eşit ağırlık."""
    v = np.maximum(vols[idx], 1e-3)
    w = 1.0 / v
    w = w / w.sum()
    cap = 2.0 / len(idx)
    for _ in range(5):
        over = w > cap
        if not over.any():
            break
        excess = float((w[over] - cap).sum())
        w[over] = cap
        under = ~over
        if under.any():
            w[under] += excess * w[under] / w[under].sum()
    return {int(c): float(x) * 0.5 for c, x in zip(idx, w)}


def pick(panel: Panel, d: int, scores_row: np.ndarray, n: int) -> tuple[dict[int, float], dict[int, float]]:
    ok = np.flatnonzero(~np.isnan(scores_row) & panel.tradable[d])
    n = min(n, len(ok) // 4)
    if n < 3:
        return {}, {}
    order = ok[np.argsort(scores_row[ok])]
    return leg_weights(panel.vol[d], order[-n:]), leg_weights(panel.vol[d], order[:n])


def simulate(panel: Panel, scores: np.ndarray, n: int, h: int, fee: float, slip: float) -> dict:
    """Günlük getiri serisi (sermayeye göre, 1x brüt, oynaklık hedeflemeli). Her h günde bir yeniden dengeleme."""
    D, S = panel.close.shape
    r1 = forward_returns(panel.close, 1)
    raw_rets = np.full(D, np.nan)
    rets = np.full(D, np.nan)
    longs: dict[int, float] = {}
    shorts: dict[int, float] = {}
    trades = []
    hold_left = 0
    cost_side = fee + slip
    target_daily = TARGET_ANNUAL_VOL / math.sqrt(365)
    for d in range(D - 1):
        cost = 0.0
        if hold_left <= 0 and not np.isnan(scores[d]).all():
            nl, ns = pick(panel, d, scores[d], n)
            if nl:
                turnover = sum(abs(nl.get(c, 0) - longs.get(c, 0)) for c in set(nl) | set(longs))
                turnover += sum(abs(ns.get(c, 0) - shorts.get(c, 0)) for c in set(ns) | set(shorts))
                cost = turnover * cost_side
                for c in set(nl) - set(longs):
                    trades.append({"symbol": panel.syms[c], "side": "LONG", "entry_time": int(panel.t[d] + DAY), "weight_pct": nl[c] * 100})
                for c in set(ns) - set(shorts):
                    trades.append({"symbol": panel.syms[c], "side": "SHORT", "entry_time": int(panel.t[d] + DAY), "weight_pct": ns[c] * 100})
                longs, shorts = nl, ns
                hold_left = h
        if longs:
            lv = sum(w * r1[d, c] for c, w in longs.items() if not np.isnan(r1[d, c]))
            sv = sum(w * r1[d, c] for c, w in shorts.items() if not np.isnan(r1[d, c]))
            raw = lv - sv
            raw_rets[d] = raw
            # oynaklık hedefleme: yalnızca GEÇMİŞ (d'den önceki) getirilere bakılır
            past = raw_rets[max(0, d - 30):d]
            past = past[~np.isnan(past)]
            scale = 1.0
            if len(past) >= 10 and past.std() > 0:
                scale = float(np.clip(target_daily / past.std(), 0.3, 1.5))
            rets[d] = scale * raw - cost
            hold_left -= 1
    return {"rets": rets, "trades": trades}


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


def _importance(kind: str, models: list[dict]) -> list[dict]:
    if not models:
        return []
    if kind.startswith("ridge"):
        W = np.array([m["coef"] for m in models])
        return sorted(
            [{"feature": FEATURES[i][0], "name": FEATURES[i][1], "coef": float(W[:, i].mean()),
              "share_pct": None, "stability_pct": float((np.sign(W[:, i]) == np.sign(W[:, i].mean())).mean() * 100.0)}
             for i in range(len(FEATURES))],
            key=lambda x: -abs(x["coef"]),
        )
    G = np.array([m["gain"] for m in models], dtype=float)
    g = G.mean(axis=0)
    tot = g.sum() or 1.0
    return sorted(
        [{"feature": FEATURES[i][0], "name": FEATURES[i][1], "coef": None, "share_pct": float(g[i] / tot * 100.0),
          "stability_pct": None} for i in range(len(FEATURES))],
        key=lambda x: -(x["share_pct"] or 0),
    )


MODEL_TR = {"ridge30": "Doğrusal model (az düzenlileştirme)", "ridge300": "Doğrusal model (güçlü düzenlileştirme)",
            "gbm": "LightGBM (karar ağaçları)"}


def run(panel: Panel, fee: float = 0.0005, slip: float = 0.0005,
        progress: Optional[Callable[[float, str], None]] = None, cancelled: Callable[[], bool] = lambda: False) -> dict:
    D = len(panel.t)
    if D < MIN_TRAIN_DAYS + 120:
        raise ValueError(f"Model için en az {MIN_TRAIN_DAYS + 120} günlük veri gerekli (şu an {D})")
    grid = list(itertools.product(available_models(), HOLD))
    score_cache: dict[tuple[str, int], np.ndarray] = {}
    model_cache: dict[tuple[str, int], list[dict]] = {}
    for gi, (kind, h) in enumerate(grid):
        if cancelled():
            from ..backtest.data import BacktestCancelled
            raise BacktestCancelled()
        msg = f"{MODEL_TR[kind]} · {h} günlük ufuk: aylık yeniden eğitim ve test"
        sc, mods = walk_forward_scores(panel, h, kind, (lambda f, gi=gi, msg=msg: progress(0.85 * (gi + f) / len(grid), msg)) if progress else None)
        score_cache[(kind, h)] = sc
        model_cache[(kind, h)] = mods
    any_sc = next(iter(score_cache.values()))
    has = np.flatnonzero(~np.isnan(any_sc).all(axis=1))
    if len(has) < 60:
        raise ValueError("Walk-forward için yeterli tahmin günü oluşmadı (likit coin sayısı az olabilir)")
    wf0, wf1 = int(has[0]), int(has[-1]) + 1
    mid = wf0 + (wf1 - wf0) // 2
    results = []
    for (kind, h), sc in score_cache.items():
        for n in TOP_N:
            sim = simulate(panel, sc, n, h, fee, slip)
            sel = metrics(sim["rets"], panel.t, wf0, mid)
            results.append({"kind": kind, "h": h, "n": n, "sel": sel, "sim": sim})
    results.sort(key=lambda r: r["sel"]["sharpe"], reverse=True)
    best = results[0]
    final = metrics(best["sim"]["rets"], panel.t, mid, wf1)
    full_wf = metrics(best["sim"]["rets"], panel.t, wf0, wf1)
    trades_final = [tr for tr in best["sim"]["trades"] if tr["entry_time"] >= int(panel.t[mid])]
    # aynı modelin diğer türlerle karşılaştırması (final döneminde; bilgi amaçlı, seçimde kullanılmadı)
    compare = []
    for kind in available_models():
        cands = [r for r in results if r["kind"] == kind]
        if cands:
            b = cands[0]
            fm = metrics(b["sim"]["rets"], panel.t, mid, wf1)
            compare.append({"kind": kind, "name": MODEL_TR[kind], "hold_days": b["h"], "top_n": b["n"],
                            "sel_sharpe": b["sel"]["sharpe"], "final_apr_pct": fm["apr_pct"], "final_t": fm["t_stat"],
                            "final_dd_pct": fm["max_drawdown_pct"]})
    # canlı kullanım için: tüm veriyle son model
    fwd = forward_returns(panel.close, best["h"])
    X, y = _training_set(panel, fwd, D - best["h"] - 1, best["h"])
    last = fit(best["kind"], X, y)
    d = D - 1
    today = []
    sc_today = np.full(len(panel.syms), np.nan)
    ok = panel.tradable[d]
    if ok.sum() >= 8:
        sc_today[ok] = predict(last, panel.X[d, ok])
    lw, sw = pick(panel, d, sc_today, best["n"])
    today = ([{"symbol": panel.syms[c], "side": "LONG", "weight_pct": w * 100} for c, w in sorted(lw.items(), key=lambda x: -x[1])]
             + [{"symbol": panel.syms[c], "side": "SHORT", "weight_pct": w * 100} for c, w in sorted(sw.items(), key=lambda x: -x[1])])
    f = final
    if (f["days"] >= 120 and f["t_stat"] >= 2.5 and f["apr_pct"] >= 10 and f["max_drawdown_pct"] <= 25
            and (f["positive_months_pct"] or 0) >= 60):
        verdict = "recommended"
    elif f["days"] >= 90 and f["t_stat"] >= 1.8 and f["apr_pct"] > 3:
        verdict = "weak"
    else:
        verdict = "none"
    if progress:
        progress(1.0, "Model hazır")
    live = {k: v for k, v in last.items() if k != "_obj"}
    live.update({"kind": best["kind"], "hold_days": best["h"], "top_n": best["n"], "features": [x[0] for x in FEATURES],
                 "trained_until": int(panel.t[-1])})
    return {
        "verdict": verdict,
        "params": {"model": best["kind"], "model_name": MODEL_TR[best["kind"]], "hold_days": best["h"], "top_n": best["n"],
                   "target_vol_pct": TARGET_ANNUAL_VOL * 100},
        "period": {"start": int(panel.t[wf0]), "split": int(panel.t[mid]), "end": int(panel.t[wf1 - 1] + DAY)},
        "selection": {k: v for k, v in best["sel"].items() if k != "monthly"},
        "final": f,
        "walk_forward": {k: v for k, v in full_wf.items() if k != "monthly"},
        "trades_final": trades_final[-2000:],
        "importance": _importance(best["kind"], model_cache[(best["kind"], best["h"])]),
        "compare": compare,
        "model": live,
        "today": today,
        "coins": len(panel.syms),
        "tradable_avg": float(panel.tradable[wf0:wf1].sum(axis=1).mean()),
    }
