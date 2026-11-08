"""Çapraz kesit özellik paneli: her gün, her coin için yalnızca o güne kadarki veriden özellikler.

Özellikler her gün coinler arasında sıralanıp [-0.5, 0.5] aralığına çevrilir (sıra normalizasyonu):
model "bu coin bugün diğerlerine göre nerede?" sorusunu öğrenir; piyasanın genel yönü çıkarılır.
Hedef: sonraki H günün getirisi, o günkü coin ortalamasından arındırılmış (piyasa-nötr).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..features.indicators import ema, rsi

DAY = 86_400_000
FEATURES = [
    ("r1", "1 günlük getiri"),
    ("r3", "3 günlük getiri"),
    ("r7", "7 günlük getiri"),
    ("r14", "14 günlük getiri"),
    ("r30", "30 günlük getiri"),
    ("vol14", "14 günlük oynaklık"),
    ("rsi14", "RSI (14 gün)"),
    ("ema20", "20 günlük ortalamaya uzaklık"),
    ("ema50", "50 günlük ortalamaya uzaklık"),
    ("dd30", "30 günlük zirveden düşüş"),
    ("range7", "7 günlük fiyat aralığı"),
    ("volz", "Hacim artışı (30 güne göre)"),
    ("funding", "Funding (son 3 gün)"),
]
F = len(FEATURES)
MIN_HISTORY = 60


@dataclass
class Panel:
    t: np.ndarray            # gün başlangıçları (ms)
    syms: list[str]
    X: np.ndarray            # [gün, coin, özellik] sıra-normalize, NaN = yok
    close: np.ndarray        # [gün, coin]
    tradable: np.ndarray     # [gün, coin] bool


def _grid(daily: dict[str, dict[str, np.ndarray]]) -> np.ndarray:
    ts = np.unique(np.concatenate([a["t"] for a in daily.values()])) if daily else np.array([], dtype=np.int64)
    return ts.astype(np.int64)


def _rank_norm(M: np.ndarray) -> np.ndarray:
    """Her satırda (gün) NaN olmayanları sıralayıp [-0.5, 0.5]'e çevirir."""
    out = np.full(M.shape, np.nan)
    for d in range(M.shape[0]):
        row = M[d]
        ok = ~np.isnan(row)
        n = int(ok.sum())
        if n < 5:
            continue
        r = np.argsort(np.argsort(row[ok]))
        out[d, ok] = r / (n - 1) - 0.5
    return out


def build_panel(daily: dict[str, dict[str, np.ndarray]], funding: Optional[dict[str, tuple[np.ndarray, np.ndarray]]] = None,
                min_qv: float = 2_000_000.0) -> Panel:
    t = _grid(daily)
    syms = sorted(daily)
    D, S = len(t), len(syms)
    C = np.full((D, S), np.nan)
    H = np.full((D, S), np.nan)
    L = np.full((D, S), np.nan)
    QV = np.full((D, S), np.nan)
    for j, s in enumerate(syms):
        a = daily[s]
        idx = np.searchsorted(t, a["t"])
        C[idx, j], H[idx, j], L[idx, j], QV[idx, j] = a["c"], a["h"], a["l"], a["qv"]
    raw = np.full((D, S, F), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        for k, n in enumerate((1, 3, 7, 14, 30)):
            raw[n:, :, k] = C[n:] / C[:-n] - 1.0
        lr = np.full((D, S), np.nan)
        lr[1:] = np.log(C[1:] / C[:-1])
        lqv = np.log1p(np.where(QV > 0, QV, np.nan))
        for j in range(S):
            col = C[:, j]
            ok = ~np.isnan(col)
            if ok.sum() < MIN_HISTORY:
                continue
            first = int(np.argmax(ok))
            seg = col[first:]
            segf = np.where(np.isnan(seg), np.nan, seg)
            # boşlukları ileri doldurarak göstergeleri hesapla (Binance günlük verisinde boşluk nadirdir)
            filled = segf.copy()
            for i in range(1, len(filled)):
                if np.isnan(filled[i]):
                    filled[i] = filled[i - 1]
            raw[first:, j, 6] = rsi(filled, 14)
            raw[first:, j, 7] = filled / ema(filled, 20) - 1.0
            raw[first:, j, 8] = filled / ema(filled, 50) - 1.0
            for i in range(first + 14, D):
                w = lr[i - 13:i + 1, j]
                w = w[~np.isnan(w)]
                if len(w) >= 10:
                    raw[i, j, 5] = float(np.std(w))
            for i in range(first + 29, D):
                raw[i, j, 9] = C[i, j] / np.nanmax(C[i - 29:i + 1, j]) - 1.0
                q = lqv[i - 30:i, j]
                q = q[~np.isnan(q)]
                if len(q) >= 20 and q.std() > 0 and not np.isnan(lqv[i, j]):
                    raw[i, j, 11] = (lqv[i, j] - q.mean()) / q.std()
            for i in range(first + 6, D):
                raw[i, j, 10] = (np.nanmax(H[i - 6:i + 1, j]) - np.nanmin(L[i - 6:i + 1, j])) / C[i, j]
        if funding:
            for j, s in enumerate(syms):
                f = funding.get(s)
                if f is None or not len(f[0]):
                    continue
                ft, fr = f
                cs = np.concatenate(([0.0], np.cumsum(fr)))
                end_t = t + DAY  # gün kapanışına kadar ödenenler
                hi = np.searchsorted(ft, end_t, side="left")
                lo = np.searchsorted(ft, end_t - 3 * DAY, side="left")
                cnt = hi - lo
                v = np.where(cnt > 0, (cs[hi] - cs[lo]) / np.maximum(cnt, 1), np.nan)
                raw[:, j, 12] = v
    X = np.stack([_rank_norm(raw[:, :, k]) for k in range(F)], axis=2)
    # eksik özellik (ör. funding verisi olmayan coin/gün) nötr = sıralamanın ortası (0)
    X = np.where(np.isnan(X), 0.0, X)
    # işlem görebilirlik: en az MIN_HISTORY gün geçmiş, son 30 günün ortalama hacmi yeterli, bugün ve yarın fiyat var
    hist_days = np.cumsum(~np.isnan(C), axis=0)
    qv30 = np.full((D, S), np.nan)
    for i in range(30, D):
        qv30[i] = np.nanmean(QV[i - 30:i], axis=0)
    tradable = (hist_days >= MIN_HISTORY) & (np.nan_to_num(qv30) >= min_qv) & ~np.isnan(C)
    return Panel(t=t, syms=syms, X=X, close=C, tradable=tradable)


def forward_returns(close: np.ndarray, h: int) -> np.ndarray:
    """[gün, coin] gün kapanışından h gün sonraki kapanışa getiri (son h gün NaN)."""
    out = np.full(close.shape, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        out[:-h] = close[h:] / close[:-h] - 1.0
    return out
