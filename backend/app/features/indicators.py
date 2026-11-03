"""Teknik göstergeler (numpy). Dizinin son elemanı en güncel bardır."""
from __future__ import annotations

import numpy as np


def _ewm_from(x: np.ndarray, alpha: float, seed: float) -> np.ndarray:
    """y[0] = seed; i ≥ 1 için y[i] = alpha·x[i] + (1 − alpha)·y[i−1] (x[0] kullanılmaz).

    Vektörel kapalı form: y[k] = r^k·(seed + Σ alpha·x[j]·r^−j), r = 1 − alpha. Taşmayı önlemek
    için r^−k ≤ e^500 olacak parçalar hâlinde hesaplanır. Python döngüsüne göre onlarca kat hızlıdır;
    girdiler (fiyat, gerçek aralık, kazanç/kayıp, DX) negatif olmadığından iptal hatası oluşmaz.
    """
    m = len(x)
    out = np.empty(m)
    if m == 0:
        return out
    out[0] = seed
    if m == 1:
        return out
    r = 1.0 - alpha
    if r <= 0.0:
        out[1:] = x[1:]
        return out
    chunk = max(1, int(500.0 / -np.log(r)))
    prev = float(seed)
    i = 1
    while i < m:
        j = min(m, i + chunk)
        rk = r ** np.arange(1, j - i + 1, dtype=float)
        seg = rk * (prev + np.cumsum(alpha * x[i:j] / rk))
        out[i:j] = seg
        prev = float(seg[-1])
        i = j
    return out


def ema(x: np.ndarray, n: int) -> np.ndarray:
    """Üstel hareketli ortalama; ilk değer basit ortalamayla tohumlanır (TA-Lib ile uyumlu)."""
    x = np.asarray(x, dtype=float)
    out = np.full(x.shape, np.nan)
    if n <= 0 or len(x) < n:
        return out
    out[n - 1 :] = _ewm_from(x[n - 1 :], 2.0 / (n + 1.0), float(x[:n].mean()))
    return out


def rma(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder yumuşatması (RSI/ATR/ADX): alpha = 1/n, basit ortalamayla tohumlanır."""
    x = np.asarray(x, dtype=float)
    out = np.full(x.shape, np.nan)
    if n <= 0 or len(x) < n:
        return out
    out[n - 1 :] = _ewm_from(x[n - 1 :], 1.0 / n, float(x[:n].mean()))
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
        # Kayan ortalama/std için kümülatif toplamlar (O(n)); ilk değere göre kaydırma
        # büyük fiyatlarda E[x²] − E[x]² iptal hatasını azaltır.
        ref = float(c[0])
        z = c - ref
        cs = np.concatenate(([0.0], np.cumsum(z)))
        cs2 = np.concatenate(([0.0], np.cumsum(z * z)))
        m = (cs[n:] - cs[:-n]) / n
        var = np.maximum((cs2[n:] - cs2[:-n]) / n - m * m, 0.0)
        mid[n - 1 :] = m + ref
        sd[n - 1 :] = np.sqrt(var)
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


def log_volume_z(v: np.ndarray, n: int) -> float:
    """Son barın hacminin, önceki n bara göre log-ölçekli z-skoru.

    Hacim log-normal dağıldığı için düz z-skoru tek bir aşırı bardan çok etkilenir;
    log ölçek daha sağlam bir "olağandışı hacim" ölçüsü verir.
    """
    v = np.asarray(v, dtype=float)
    if len(v) < n + 1:
        return 0.0
    return zscore_last(np.log1p(np.maximum(v[-n - 1 :], 0.0)), n)


def aggregate_bars(a: dict[str, np.ndarray], step_ms: int, closed_only: bool = True) -> dict[str, np.ndarray]:
    """Mumları daha büyük zaman dilimine birleştirir (UTC sınırlarına hizalı).

    `closed_only` ise son grup tamamlanmamışsa atılır.
    """
    t = a["t"]
    if len(t) == 0:
        return {k: v[:0] for k, v in a.items()}
    keys = t - (t % step_ms)
    starts = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]])
    ends = np.r_[starts[1:], len(t)]
    out = {
        "t": keys[starts],
        "o": a["o"][starts],
        "h": np.maximum.reduceat(a["h"], starts),
        "l": np.minimum.reduceat(a["l"], starts),
        "c": a["c"][ends - 1],
        "v": np.add.reduceat(a["v"], starts),
        "qv": np.add.reduceat(a["qv"], starts),
        "tbq": np.add.reduceat(a["tbq"], starts),
    }
    if closed_only and len(starts):
        base_step = int(t[1] - t[0]) if len(t) > 1 else step_ms
        last_complete = (t[-1] + base_step) >= keys[-1] + step_ms
        if not last_complete:
            out = {k: v[:-1] for k, v in out.items()}
    return out


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
    bm = b - b.mean()
    var = float(np.dot(bm, bm)) / n
    if var <= 0:
        return 1.0
    return float(np.dot(a - a.mean(), bm) / n / var)
