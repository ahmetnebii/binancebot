"""S5 — Klasik grafik formasyonları (1 saatlik).

Formasyonlar dönüm noktalarından (pivot) nesnel kurallarla tanınır; göze göre yorum yoktur:
- Ters omuz-baş-omuz / omuz-baş-omuz: üç dip (tepe), ortadaki en derin, omuzlar benzer seviyede;
  giriş boyun çizgisinin TAZE kırılımında.
- İkili dip / ikili tepe: benzer seviyede iki dip (tepe), aradaki tepenin (dibin) kırılımı.
- Yükselen / alçalan üçgen: yatay direnç (destek) + yükselen dipler (alçalan tepeler), kırılım.

Pivotlar yalnızca sağında k mum oluştuktan sonra kesinleşir (gelecek bilgisi kullanılmaz).
Hedef: formasyon yüksekliği kadar ölçülü hareket. Stop: son omuz / dip / yükselen dibin ötesi.
Ayı formasyonları, fiyat serisi ters çevrilerek (−düşük, −yüksek, −kapanış) aynı kodla bulunur.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from ..features.indicators import atr, log_volume_z
from ..models import Signal
from .base import StrategyContext, clamp01, make_signal

WINDOW = 240
MAX_AGE = 40  # son pivot ile kırılım arasında en fazla bu kadar mum


def pivots(x: np.ndarray, k: int, high: bool) -> np.ndarray:
    """k-sol/k-sağ pencerede en yüksek (high) / en düşük nokta indeksleri; düzlüklerde ilki."""
    n = len(x)
    if n < 2 * k + 1:
        return np.array([], dtype=int)
    win = sliding_window_view(x, 2 * k + 1)
    ext = win.max(axis=1) if high else win.min(axis=1)
    centers = np.arange(k, n - k)
    idx = centers[x[k : n - k] == ext]
    out: list[int] = []
    for i in idx:
        if not out or i - out[-1] > k:
            out.append(int(i))
    return np.array(out, dtype=int)


@dataclass
class Found:
    kind: str       # "hs" | "double" | "triangle"
    level: float    # kırılım seviyesi (dönüştürülmüş uzayda)
    stop: float     # stop (dönüştürülmüş uzayda)
    height: float   # formasyon yüksekliği (ölçülü hareket)
    width: int      # formasyonun mum genişliği


def _fresh(c: np.ndarray, line: np.ndarray, since: int) -> bool:
    """Son mum çizgiyi yukarı kırdı mı ve formasyon tamamlandıktan sonra daha önce kırmamış mı?"""
    n = len(c)
    if not (c[-1] > line[-1] and c[-2] <= line[-2]):
        return False
    seg = slice(since, n - 1)
    return bool(np.all(c[seg] <= line[seg]))


def detect_bullish(h: np.ndarray, l: np.ndarray, c: np.ndarray, at: float, k: int, tol: float) -> Optional[Found]:  # noqa: E741
    n = len(c)
    ph = pivots(h, k, True)
    pl = pivots(l, k, False)
    t = np.arange(n, dtype=float)

    # 1) Ters omuz-baş-omuz
    if len(pl) >= 3:
        ia, ib, id_ = (int(v) for v in pl[-3:])
        ls, hd, rs = l[ia], l[ib], l[id_]
        if (
            n - 1 - id_ <= MAX_AGE and id_ - ia <= 160
            and min(ls, rs) - hd >= 0.75 * at and abs(ls - rs) <= tol * at
        ):
            left = [int(i) for i in ph if ia < i < ib]
            right = [int(i) for i in ph if ib < i < id_]
            if left and right:
                n1 = max(left, key=lambda i: h[i])
                n2 = max(right, key=lambda i: h[i])
                if n2 > n1 and abs(h[n2] - h[n1]) <= 2.0 * at:
                    slope = (h[n2] - h[n1]) / (n2 - n1)
                    neck = h[n1] + slope * (t - n1)
                    if _fresh(c, neck, id_):
                        height = float(neck[ib] - hd)
                        return Found("hs", float(neck[-1]), float(rs - 0.25 * at), height, id_ - ia)

    # 2) İkili dip
    if len(pl) >= 2:
        ia, ib = int(pl[-2]), int(pl[-1])
        lo = min(l[ia], l[ib])
        if n - 1 - ib <= MAX_AGE and 10 <= ib - ia <= 120 and abs(l[ia] - l[ib]) <= tol * at:
            peak = float(h[ia : ib + 1].max())
            if peak - max(l[ia], l[ib]) >= 2.0 * at:
                line = np.full(n, peak)
                if _fresh(c, line, ib):
                    return Found("double", peak, float(lo - 0.25 * at), float(peak - lo), ib - ia)

    # 3) Yükselen üçgen (yatay direnç + yükselen dipler)
    if len(ph) >= 2 and len(pl) >= 2:
        ih1, ih2 = int(ph[-2]), int(ph[-1])
        il1, il2 = int(pl[-2]), int(pl[-1])
        res = float(max(h[ih1], h[ih2]))
        if (
            n - 1 - max(ih2, il2) <= MAX_AGE and ih2 - ih1 >= 6
            and abs(h[ih1] - h[ih2]) <= 0.5 * tol * at
            and il2 > ih1 and l[il2] - l[il1] >= 0.5 * at and il1 < il2
            and res - l[il1] >= 2.0 * at
        ):
            line = np.full(n, res)
            if _fresh(c, line, max(ih2, il2)):
                return Found("triangle", res, float(l[il2] - 0.25 * at), float(res - l[il1]), max(ih2, il2) - min(ih1, il1))
    return None


NAMES = {
    ("hs", "LONG"): "Ters omuz-baş-omuz",
    ("hs", "SHORT"): "Omuz-baş-omuz",
    ("double", "LONG"): "İkili dip",
    ("double", "SHORT"): "İkili tepe",
    ("triangle", "LONG"): "Yükselen üçgen",
    ("triangle", "SHORT"): "Alçalan üçgen",
}
BASE_SCORE = {"hs": 10.0, "double": 8.0, "triangle": 6.0}
KIND_CODE = {"hs": 1.0, "double": 2.0, "triangle": 3.0}


class ChartPatterns:
    code = "S5"
    name = "Grafik Formasyonu"
    timeframe = "1h"

    def evaluate(self, symbol: str, ctx: StrategyContext) -> Optional[Signal]:
        cfg = ctx.settings
        a = ctx.arrays(symbol, "1h", 120)
        if a is None:
            return None
        if ctx.regime.name == "CHAOS":
            return None
        h, l, c, qv = a["h"][-WINDOW:], a["l"][-WINDOW:], a["c"][-WINDOW:], a["qv"][-WINDOW:]  # noqa: E741
        at = float(atr(h, l, c, 14)[-1])
        if not math.isfinite(at) or at <= 0:
            return None
        k = int(cfg.s5_pivot)
        tol = float(cfg.s5_tol)
        found = detect_bullish(h, l, c, at, k, tol)
        side = "LONG"
        if found is None:
            found = detect_bullish(-l, -h, -c, at, k, tol)
            side = "SHORT"
        if found is None:
            return None
        sgn = 1 if side == "LONG" else -1
        close = float(c[-1])
        stop = found.stop * sgn  # dönüştürülmüş uzaydan geri
        dist = abs(close - stop) / close
        if sgn * (close - stop) <= 0 or dist > 0.08:
            return None
        if dist < 0.008:
            stop = close - sgn * 0.008 * close
        risk = abs(close - stop)
        vol_z = log_volume_z(qv, 48)
        if vol_z < cfg.s5_vol_z:
            return None
        mm_r = found.height / risk if risk > 0 else 0.0
        if mm_r < 0.8:  # hedef riskten küçükse anlamsız
            return None
        tp1 = min(2.5, max(1.0, 0.6 * mm_r))
        tp2 = min(5.0, max(tp1 + 0.5, mm_r))
        fr = ctx.funding_rate(symbol)
        reg = ctx.regime.name
        aligned = (reg == "BULL" and side == "LONG") or (reg == "BEAR" and side == "SHORT")
        score = (
            50 + BASE_SCORE[found.kind] + clamp01(vol_z / 3.0) * 15 + (8 if aligned else 0)
            + clamp01((mm_r - 1.0) / 2.0) * 10 - clamp01(sgn * fr / 0.0005) * 10
        )
        pname = NAMES[(found.kind, side)]
        level = found.level * sgn
        reasons = [
            f"{pname} formasyonu ({found.width} mum genişliğinde) tamamlandı",
            f"{'Boyun çizgisi' if found.kind == 'hs' else 'Kırılım seviyesi'} {level:.6g} {'yukarı' if side == 'LONG' else 'aşağı'} kırıldı (taze kırılım)",
            f"Ölçülü hareket hedefi ≈ {mm_r:.1f}R (formasyon yüksekliği kadar)",
            f"Kırılım mumunda hacim z-skoru {vol_z:.1f}",
        ]
        warnings: list[str] = []
        if not aligned and reg in ("BULL", "BEAR"):
            warnings.append("Formasyon genel piyasa yönüne ters")
        if vol_z < 1.0:
            warnings.append("Kırılım hacmi zayıf: sahte kırılım riski")
        return make_signal(
            symbol=symbol, side=side, strategy=self.code, strategy_name=f"{self.name}: {pname}", timeframe=self.timeframe,
            bar_time=int(a["t"][-1]), now=ctx.now, ttl_ms=max(ctx.scanner.signal_ttl_min, 45) * 60_000,
            entry=close, stop=stop, tp_spec=[(round(tp1, 2), 0.5), (round(tp2, 2), 0.5)], atr_value=at,
            score=score, reasons=reasons, warnings=warnings,
            features={"pattern": KIND_CODE[found.kind], "mm_r": mm_r, "vol_z": vol_z, "width": float(found.width),
                      "funding": fr, "atr_pct": at / close * 100.0},
            trailing_atr_mult=3.0, time_stop_bars=36,
        )
