"""Performans metrikleri: hem canlı geçmiş hem backtest sonuçları için ortak."""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence

import numpy as np


def _f(x: float) -> Optional[float]:
    return float(x) if x is not None and math.isfinite(float(x)) else None


def r_histogram(rs: Sequence[float], width: float = 0.5, lo: float = -3.0, hi: float = 5.0) -> list[dict]:
    """R katlarının dağılımı; uç değerler ilk/son kutuya toplanır."""
    edges = list(np.arange(lo, hi + 1e-9, width))
    bins = [{"from": round(edges[i], 4), "to": round(edges[i + 1], 4), "count": 0} for i in range(len(edges) - 1)]
    if not bins:
        return []
    for r in rs:
        if r is None or not math.isfinite(r):
            continue
        idx = int((r - lo) // width)
        idx = max(0, min(len(bins) - 1, idx))
        bins[idx]["count"] += 1
    return bins


def month_key(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m")


def monthly_pnl(items: Iterable[tuple[int, float]]) -> list[dict]:
    """(kapanış zamanı ms, net K/Z) → aylık toplamlar."""
    out: dict[str, dict] = {}
    for ts, pnl in items:
        k = month_key(ts)
        d = out.setdefault(k, {"month": k, "pnl": 0.0, "trades": 0})
        d["pnl"] += pnl
        d["trades"] += 1
    return [out[k] for k in sorted(out)]


def monthly_returns(times_s: np.ndarray, equity: np.ndarray) -> list[dict]:
    """Özsermaye eğrisinden aylık getiri (%)."""
    if len(times_s) < 2:
        return []
    out = []
    cur_key = None
    start_eq = float(equity[0])
    last_eq = start_eq
    for t, e in zip(times_s, equity):
        k = month_key(int(t) * 1000)
        if cur_key is None:
            cur_key = k
        if k != cur_key:
            out.append({"month": cur_key, "return_pct": (last_eq / start_eq - 1.0) * 100.0 if start_eq else 0.0})
            cur_key = k
            start_eq = last_eq
        last_eq = float(e)
    out.append({"month": cur_key, "return_pct": (last_eq / start_eq - 1.0) * 100.0 if start_eq else 0.0})
    return out


def drawdown_series(equity: np.ndarray) -> np.ndarray:
    if len(equity) == 0:
        return equity
    peak = np.maximum.accumulate(equity)
    return np.where(peak > 0, (equity - peak) / peak * 100.0, 0.0)


def daily_returns(times_s: np.ndarray, equity: np.ndarray) -> np.ndarray:
    if len(times_s) < 2:
        return np.array([])
    days = (times_s // 86400).astype(np.int64)
    idx = np.flatnonzero(np.r_[days[1:] != days[:-1], True])
    closes = equity[idx]
    if len(closes) < 2:
        return np.array([])
    return closes[1:] / closes[:-1] - 1.0


def sharpe_sortino(daily: np.ndarray) -> tuple[Optional[float], Optional[float]]:
    if len(daily) < 10:
        return None, None
    mu = float(daily.mean())
    sd = float(daily.std(ddof=1))
    neg = daily[daily < 0]
    dsd = float(np.sqrt((neg ** 2).mean())) if len(neg) else 0.0
    sharpe = mu / sd * math.sqrt(365) if sd > 0 else None
    sortino = mu / dsd * math.sqrt(365) if dsd > 0 else None
    return _f(sharpe) if sharpe is not None else None, _f(sortino) if sortino is not None else None


def trade_stats(rs: Sequence[float], pnls: Sequence[float]) -> dict:
    n = len(rs)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p <= 0)
    return {
        "trades": n,
        "win_rate": (len(wins) / n) if n else None,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "expectancy_r": (sum(rs) / n) if n else None,
        "avg_win_r": (sum(wins) / len(wins)) if wins else None,
        "avg_loss_r": (sum(losses) / len(losses)) if losses else None,
        "best_r": max(rs) if rs else None,
        "worst_r": min(rs) if rs else None,
        "total_pnl": float(sum(pnls)),
    }


def monte_carlo(rs: Sequence[float], risk_fraction: float, initial: float, runs: int = 1000, seed: int = 11) -> Optional[dict]:
    """İşlem R dizisini yeniden örnekleyerek (bootstrap) düşüş ve sonuç dağılımı.

    Her işlemde özsermayenin `risk_fraction` kadarı riske edilir (sabit kesirli boyutlandırma).
    """
    arr = np.asarray([r for r in rs if r is not None and math.isfinite(r)], dtype=float)
    if len(arr) < 10 or risk_fraction <= 0:
        return None
    rng = np.random.default_rng(seed)
    samples = rng.choice(arr, size=(runs, len(arr)), replace=True)
    growth = np.clip(1.0 + risk_fraction * samples, 1e-6, None)
    paths = initial * np.cumprod(growth, axis=1)
    paths = np.concatenate([np.full((runs, 1), initial), paths], axis=1)
    peaks = np.maximum.accumulate(paths, axis=1)
    dds = ((peaks - paths) / peaks).max(axis=1) * 100.0
    finals = paths[:, -1]
    return {
        "runs": runs,
        "dd_p50": float(np.percentile(dds, 50)),
        "dd_p95": float(np.percentile(dds, 95)),
        "dd_p99": float(np.percentile(dds, 99)),
        "final_p5": float(np.percentile(finals, 5)),
        "final_p50": float(np.percentile(finals, 50)),
        "final_p95": float(np.percentile(finals, 95)),
        "prob_dd_20": float((dds >= 20.0).mean()),
        "prob_dd_30": float((dds >= 30.0).mean()),
    }


def walk_forward(trades: list[dict], times_s: np.ndarray, equity: np.ndarray, start_ms: int, end_ms: int, folds: int) -> list[dict]:
    """Dönemsel tutarlılık: test aralığını eşit parçalara bölüp her parçayı ayrı değerlendirir."""
    if folds < 1 or end_ms <= start_ms:
        return []
    out = []
    span = (end_ms - start_ms) / folds
    for i in range(folds):
        a = int(start_ms + i * span)
        b = int(start_ms + (i + 1) * span)
        ft = [t for t in trades if a <= t["exit_time"] < b]
        rs = [t["r"] for t in ft]
        pn = [t["pnl"] for t in ft]
        st = trade_stats(rs, pn)
        ret = 0.0
        if len(times_s):
            ia = int(np.searchsorted(times_s, a // 1000))
            ib = int(np.searchsorted(times_s, b // 1000)) - 1
            ia = min(max(ia, 0), len(equity) - 1)
            ib = min(max(ib, 0), len(equity) - 1)
            if equity[ia] > 0:
                ret = (float(equity[ib]) / float(equity[ia]) - 1.0) * 100.0
        out.append({
            "fold": i + 1, "start": a, "end": b, "trades": st["trades"],
            "expectancy_r": st["expectancy_r"], "profit_factor": st["profit_factor"], "return_pct": ret,
        })
    return out


def downsample(times_s: np.ndarray, *series: np.ndarray, max_points: int = 1500) -> tuple:
    n = len(times_s)
    if n <= max_points:
        return (times_s, *series)
    idx = np.linspace(0, n - 1, max_points).astype(int)
    return (times_s[idx], *(s[idx] for s in series))


def now_s() -> int:
    return int(time.time())
