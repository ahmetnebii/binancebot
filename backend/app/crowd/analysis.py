"""Kalabalık analizi: "kalabalık aşırı bir tarafa yığıldığında fiyat sonra ne yaptı?"

Toplanan saatlik veriden, her coin için kendi son 7 günlük ortalamasına göre (yalnızca geçmiş) z-skoru
hesaplanır. z > +1 (kalabalık o yönde aşırı) ve z < −1 olan saatlerden sonraki 24 saatlik fiyat getirisi
karşılaştırılır. Çakışan pencereleri saymamak için her coinde 24 saatte bir gözlem alınır.
Fiyat, açık pozisyonun USDT değeri / adedi ile elde edilir (aynı kaynaktan, ek istek gerekmez).
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

HOUR = 3_600_000
LOOKBACK = 168     # z-skoru için geçmiş saat sayısı (7 gün)
HORIZON = 24       # ileri getiri (saat)

FEATURES = {
    "ls_ratio": ("Tüm hesaplar long/short oranı", "Hesapların çoğu long"),
    "top_ls": ("Büyük hesaplar long/short pozisyon oranı", "Büyük hesaplar ağırlıkla long"),
    "taker_ratio": ("Agresif alım/satım oranı", "Piyasa emirleriyle agresif alım"),
    "oi_chg": ("Açık pozisyon değişimi (24 saat)", "Açık pozisyon hızla artıyor"),
    "funding": ("Funding oranı", "Long'lar yüksek funding ödüyor"),
}


def _rolling_z(x: np.ndarray, n: int, at: Optional[np.ndarray] = None) -> np.ndarray:
    """i noktasında, ÖNCEKİ n değere göre z-skoru (yalnızca geçmiş). at verilirse sadece o indekslerde hesaplanır."""
    out = np.full(len(x), np.nan)
    for i in (at if at is not None else range(n, len(x))):
        if i < n:
            continue
        w = x[i - n:i]
        w = w[~np.isnan(w)]
        if len(w) < n // 2 or np.isnan(x[i]):
            continue
        sd = float(np.std(w))
        if sd > 0:
            out[i] = (x[i] - float(np.mean(w))) / sd
    return out


def _stat(v: list[float]) -> dict:
    n = len(v)
    if n == 0:
        return {"n": 0, "mean_pct": None, "t": None, "up_pct": None}
    a = np.asarray(v)
    sd = float(a.std(ddof=1)) if n > 1 else 0.0
    return {"n": n, "mean_pct": float(a.mean()) * 100.0, "t": float(a.mean() / sd * math.sqrt(n)) if sd > 0 else None,
            "up_pct": float((a > 0).mean()) * 100.0}


def analyze(crowd_rows: list[tuple], funding_rows: list[tuple]) -> dict:
    by: dict[str, list[tuple]] = {}
    for r in crowd_rows:
        by.setdefault(r[0], []).append(r)
    fby: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    tmp: dict[str, list[tuple[int, float]]] = {}
    for s, t, rate in funding_rows:
        tmp.setdefault(s, []).append((t, rate))
    for s, v in tmp.items():
        fby[s] = (np.array([x[0] for x in v], dtype=np.int64), np.array([x[1] for x in v]))

    obs: dict[str, dict[str, list[float]]] = {f: {"high": [], "low": [], "all": []} for f in FEATURES}
    first: Optional[int] = None
    last: Optional[int] = None
    for sym, rows in by.items():
        t = np.array([r[1] for r in rows], dtype=np.int64)
        oi = np.array([np.nan if r[2] is None else r[2] for r in rows], float)
        oiv = np.array([np.nan if r[3] is None else r[3] for r in rows], float)
        if len(t) < LOOKBACK + HORIZON + 10:
            continue
        first = int(t[0]) if first is None else min(first, int(t[0]))
        last = int(t[-1]) if last is None else max(last, int(t[-1]))
        with np.errstate(invalid="ignore", divide="ignore"):
            price = oiv / oi
        idx = {int(x): i for i, x in enumerate(t)}
        fwd = np.full(len(t), np.nan)
        for i, ti in enumerate(t):
            j = idx.get(int(ti) + HORIZON * HOUR)
            if j is not None and price[i] > 0 and price[j] > 0:
                fwd[i] = price[j] / price[i] - 1.0
        feats = {
            "ls_ratio": np.array([np.nan if r[4] is None else r[4] for r in rows], float),
            "top_ls": np.array([np.nan if r[5] is None else r[5] for r in rows], float),
            "taker_ratio": np.array([np.nan if r[6] is None else r[6] for r in rows], float),
        }
        oc = np.full(len(t), np.nan)
        for i, ti in enumerate(t):
            j = idx.get(int(ti) - 24 * HOUR)
            if j is not None and oi[j] > 0:
                oc[i] = oi[i] / oi[j] - 1.0
        feats["oi_chg"] = oc
        if sym in fby:
            ft, fr = fby[sym]
            k = np.searchsorted(ft, t, side="right") - 1
            feats["funding"] = np.where(k >= 0, fr[np.clip(k, 0, len(fr) - 1)], np.nan)
        else:
            feats["funding"] = np.full(len(t), np.nan)
        sample = np.zeros(len(t), dtype=bool)
        sample[LOOKBACK::HORIZON] = True  # 24 saatte bir, çakışmasız
        at = np.flatnonzero(sample & ~np.isnan(fwd))
        for f, x in feats.items():
            z = _rolling_z(x, LOOKBACK, at)
            for i in np.flatnonzero(sample & ~np.isnan(fwd) & ~np.isnan(z)):
                obs[f]["all"].append(float(fwd[i]))
                if z[i] > 1.0:
                    obs[f]["high"].append(float(fwd[i]))
                elif z[i] < -1.0:
                    obs[f]["low"].append(float(fwd[i]))
    days = ((last - first) / 86_400_000) if first is not None and last is not None else 0.0
    results = []
    for f, (name, high_desc) in FEATURES.items():
        hi, lo, al = _stat(obs[f]["high"]), _stat(obs[f]["low"]), _stat(obs[f]["all"])
        diff = None
        diff_t = None
        if hi["n"] >= 5 and lo["n"] >= 5:
            a, b = np.asarray(obs[f]["high"]), np.asarray(obs[f]["low"])
            diff = (float(a.mean()) - float(b.mean())) * 100.0
            se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
            diff_t = (float(a.mean()) - float(b.mean())) / se if se > 0 else None
        if diff_t is None or abs(diff_t) < 2.0:
            reading = "Anlamlı bir fark yok (ya da henüz veri yetersiz)."
        elif diff < 0:
            reading = f"'{high_desc}' olduğunda fiyat sonraki 24 saatte görece DÜŞMÜŞ: kalabalığın tersi kazandırmış."
        else:
            reading = f"'{high_desc}' olduğunda fiyat sonraki 24 saatte görece YÜKSELMİŞ: kalabalıkla aynı yön kazandırmış."
        results.append({"feature": f, "name": name, "high_desc": high_desc, "high": hi, "low": lo, "all": al,
                        "diff_pct": diff, "diff_t": diff_t, "reading": reading})
    enough = days >= 60
    return {
        "days": days, "symbols": len(by), "enough": enough, "results": results,
        "note": ("Yeterli veri var; yine de |t| ≥ 3 olmayan farkları tesadüf sayın." if enough else
                 f"Şu an {days:.0f} günlük veri var. Güvenilir sonuç için en az 60-90 gün gerekir; toplayıcı her saat veri eklemeye devam ediyor."),
    }
