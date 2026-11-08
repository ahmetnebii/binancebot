"""Çapraz kesit özellik paneli: her gün, her coin için yalnızca o güne kadarki veriden özellikler.

Özellikler her gün coinler arasında sıralanıp [-0.5, 0.5] aralığına çevrilir (sıra normalizasyonu):
model "bu coin bugün diğerlerine göre nerede?" sorusunu öğrenir; piyasanın genel yönü çıkarılır.

İşlem evreni (tradable) bilerek dar tutulur — küçük, sığ ve yeni coinlerdeki ani pump/dump'lar
hem modelin öğrendiğini bozar hem de testte varsayılandan çok daha yüksek kayma demektir:
- son 30 günün ortalama günlük hacmi ≥ min_qv (varsayılan 20M USDT)
- en az 90 günlük geçmiş (yeni listelenen coin yok)
- son 1 günde |getiri| ≤ %25 ve son 3 günde ≤ %50 (aşırı hareket eden coine girilmez)
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
    ("beta60", "Piyasaya duyarlılık (beta, 60 gün)"),
    ("max30", "30 günün en büyük günlük sıçraması"),
    ("qvtrend", "Hacim trendi (7 gün / 30 gün)"),
]
F = len(FEATURES)
MIN_HISTORY = 90
MAX_ABS_R1 = 0.25
MAX_ABS_R3 = 0.50


@dataclass
class Panel:
    t: np.ndarray            # gün başlangıçları (ms)
    syms: list[str]
    X: np.ndarray            # [gün, coin, özellik] sıra-normalize (eksik = 0, nötr)
    close: np.ndarray        # [gün, coin]
    tradable: np.ndarray     # [gün, coin] bool
    vol: np.ndarray          # [gün, coin] 14 günlük günlük oynaklık (ham; pozisyon boyu ve hedef için)


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


def _rolling_mean(M: np.ndarray, n: int) -> np.ndarray:
    """[gün, coin] için son n günün (bugün dahil) NaN'siz ortalaması; yeterli gözlem yoksa NaN."""
    x = np.where(np.isnan(M), 0.0, M)
    c = np.where(np.isnan(M), 0.0, 1.0)
    cs = np.vstack([np.zeros((1, M.shape[1])), np.cumsum(x, axis=0)])
    cc = np.vstack([np.zeros((1, M.shape[1])), np.cumsum(c, axis=0)])
    out = np.full(M.shape, np.nan)
    if M.shape[0] >= n:
        s = cs[n:] - cs[:-n]
        k = cc[n:] - cc[:-n]
        with np.errstate(invalid="ignore", divide="ignore"):
            out[n - 1:] = np.where(k >= max(3, n // 2), s / np.maximum(k, 1), np.nan)
    return out


def build_panel(daily: dict[str, dict[str, np.ndarray]], funding: Optional[dict[str, tuple[np.ndarray, np.ndarray]]] = None,
                min_qv: float = 20_000_000.0) -> Panel:
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
        # oynaklık (vektörel): E[x²] − E[x]²
        m1 = _rolling_mean(lr, 14)
        m2 = _rolling_mean(lr * lr, 14)
        vol = np.sqrt(np.maximum(m2 - m1 * m1, 0.0))
        raw[:, :, 5] = vol
        lqv = np.log1p(np.where(QV > 0, QV, np.nan))
        # piyasa getirisi (eşit ağırlık) ve beta
        cnt = (~np.isnan(lr)).sum(axis=1)
        mkt = np.where(cnt > 0, np.nansum(lr, axis=1) / np.maximum(cnt, 1), np.nan)
        mk = np.repeat(mkt[:, None], S, axis=1)
        mk = np.where(np.isnan(lr), np.nan, mk)
        cov = _rolling_mean(lr * mk, 60) - _rolling_mean(lr, 60) * _rolling_mean(mk, 60)
        var = _rolling_mean(mk * mk, 60) - _rolling_mean(mk, 60) ** 2
        raw[:, :, 13] = np.where(var > 0, cov / var, np.nan)
        # 7 ve 30 günlük hacim ortalamaları
        qv7 = _rolling_mean(QV, 7)
        qv30 = _rolling_mean(QV, 30)
        raw[:, :, 15] = np.log(qv7 / qv30)
        for j in range(S):
            col = C[:, j]
            ok = ~np.isnan(col)
            if ok.sum() < 60:
                continue
            first = int(np.argmax(ok))
            filled = col[first:].copy()
            for i in range(1, len(filled)):
                if np.isnan(filled[i]):
                    filled[i] = filled[i - 1]
            raw[first:, j, 6] = rsi(filled, 14)
            raw[first:, j, 7] = filled / ema(filled, 20) - 1.0
            raw[first:, j, 8] = filled / ema(filled, 50) - 1.0
            for i in range(first + 29, D):
                w = C[i - 29:i + 1, j]
                raw[i, j, 9] = C[i, j] / np.nanmax(w) - 1.0
                r = raw[i - 29:i + 1, j, 0]
                if (~np.isnan(r)).sum() >= 20:
                    raw[i, j, 14] = np.nanmax(r)
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
                raw[:, j, 12] = np.where(cnt > 0, (cs[hi] - cs[lo]) / np.maximum(cnt, 1), np.nan)
    X = np.stack([_rank_norm(raw[:, :, k]) for k in range(F)], axis=2)
    X = np.where(np.isnan(X), 0.0, X)  # eksik özellik nötr (sıralamanın ortası)
    hist_days = np.cumsum(~np.isnan(C), axis=0)
    r1 = raw[:, :, 0]
    r3 = raw[:, :, 1]
    tradable = (
        (hist_days >= MIN_HISTORY) & (np.nan_to_num(qv30) >= min_qv) & ~np.isnan(C) & ~np.isnan(vol) & (vol > 0)
        & (np.abs(np.nan_to_num(r1)) <= MAX_ABS_R1) & (np.abs(np.nan_to_num(r3)) <= MAX_ABS_R3)
    )
    return Panel(t=t, syms=syms, X=X, close=C, tradable=tradable, vol=np.nan_to_num(vol, nan=0.0))


def forward_returns(close: np.ndarray, h: int) -> np.ndarray:
    """[gün, coin] gün kapanışından h gün sonraki kapanışa getiri (son h gün NaN)."""
    out = np.full(close.shape, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        out[:-h] = close[h:] / close[:-h] - 1.0
    return out
