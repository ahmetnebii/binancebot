"""Teknik göstergeler (numpy). Dizinin son elemanı en güncel bardır."""
from __future__ import annotations

import numpy as np


def ema(x: np.ndarray, n: int) -> np.ndarray:
    """Üstel hareketli ortalama; ilk değer basit ortalamayla tohumlanır (TA-Lib ile uyumlu)."""
    x = np.asarray(x, dtype=float)
    out = np.full(x.shape, np.nan)
    if n <= 0 or len(x) < n:
        return out
    alpha = 2.0 / (n + 1.0)
    prev = float(x[:n].mean())
    out[n - 1] = prev
    for i in range(n, len(x)):
        prev = alpha * x[i] + (1.0 - alpha) * prev
        out[i] = prev
    return out


def rma(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder yumuşatması (RSI/ATR/ADX)."""
    x = np.asarray(x, dtype=float)
    out = np.full(x.shape, np.nan)
    if n <= 0 or len(x) < n:
        return out
    prev = float(x[:n].mean())
    out[n - 1] = prev
    for i in range(n, len(x)):
        prev = (prev * (n - 1) + x[i]) / n
        out[i] = prev
    return out


def true_range(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:  # noqa: E741
    tr = np.empty(len(h))
    if len(h) == 0:
        return tr
    tr[0] = h[0] - l[0]
    prev_c = c[:-1]
    tr[1:] = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - prev_c), np.abs(l[1:] - prev_c)))
    return tr


def atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:  # noqa: E741
    return rma(true_range(h, l, c), n)


def rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    c = np.asarray(c, dtype=float)
    out = np.full(c.shape, np.nan)
    if len(c) <= n:
        return out
    d = np.diff(c)
    gain = np.where(d > 0, d, 0.0)
    loss = np.where(d < 0, -d, 0.0)
    ag = rma(gain, n)
    al = rma(loss, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = ag / al
        r = 100.0 - 100.0 / (1.0 + rs)
    r = np.where(al == 0, 100.0, r)
    out[1:] = r
    return out


def adx(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:  # noqa: E741
    out = np.full(len(h), np.nan)
    if len(h) < 2 * n + 1:
        return out
    up = h[1:] - h[:-1]
    dn = l[:-1] - l[1:]
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = true_range(h, l, c)[1:]
    atr_ = rma(tr, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100.0 * rma(plus_dm, n) / atr_
        mdi = 100.0 * rma(minus_dm, n) / atr_
        dx = 100.0 * np.abs(pdi - mdi) / (pdi + mdi)
    dx = np.nan_to_num(dx, nan=0.0)
    valid = ~np.isnan(atr_)
    first = int(np.argmax(valid)) if valid.any() else len(dx)
    adx_part = rma(dx[first:], n)
    out[1 + first :] = adx_part
    return out


def bollinger(c: np.ndarray, n: int = 20, k: float = 2.0) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    c = np.asarray(c, dtype=float)
    mid = np.full(c.shape, np.nan)
    sd = np.full(c.shape, np.nan)
    if len(c) >= n:
        windows = np.lib.stride_tricks.sliding_window_view(c, n)
        mid[n - 1 :] = windows.mean(axis=1)
        sd[n - 1 :] = windows.std(axis=1)
    upper = mid + k * sd
    lower = mid - k * sd
    with np.errstate(divide="ignore", invalid="ignore"):
        width = (upper - lower) / mid
    return mid, upper, lower, width


def zscore_last(x: np.ndarray, n: int) -> float:
    """Son değerin, önceki n değere göre z-skoru."""
    if len(x) < n + 1:
        return 0.0
    ref = np.asarray(x[-n - 1 : -1], dtype=float)
    sd = float(ref.std())
    if sd <= 0:
        return 0.0
    return float((x[-1] - ref.mean()) / sd)


def pct_rank_last(x: np.ndarray, n: int) -> float:
    """Son değerin önceki n değer içindeki yüzdelik sırası (0-1)."""
    if len(x) < n + 1:
        return 0.5
    ref = np.asarray(x[-n - 1 : -1], dtype=float)
    ref = ref[~np.isnan(ref)]
    if len(ref) == 0 or np.isnan(x[-1]):
        return 0.5
    return float((ref < x[-1]).mean())


def log_returns(c: np.ndarray) -> np.ndarray:
    c = np.asarray(c, dtype=float)
    return np.diff(np.log(c))


def correlation(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    if n < 10:
        return 0.0
    a2, b2 = a[-n:], b[-n:]
    if a2.std() == 0 or b2.std() == 0:
        return 0.0
    return float(np.corrcoef(a2, b2)[0, 1])


def beta(asset: np.ndarray, bench: np.ndarray) -> float:
    n = min(len(asset), len(bench))
    if n < 10:
        return 1.0
    a, b = asset[-n:], bench[-n:]
    var = float(b.var())
    if var <= 0:
        return 1.0
    return float(np.cov(a, b, ddof=0)[0, 1] / var)
