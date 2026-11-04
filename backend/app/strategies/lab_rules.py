"""Strateji Laboratuvarı kural dili (S6).

Bir "kural" = zaman dilimi + giriş tetikleyicisi + 0-2 filtre + stop + çıkış planı + zaman stopu + yön.
Aynı vektörel sinyal kodu üç yerde kullanılır: laboratuvarın hızlı taraması, tam portföy
simülasyonu ve canlı tarama. Böylece bulunan kural canlıda birebir aynı sinyali üretir.

Tüm göstergeler nedenseldir (yalnızca o ana kadarki mumlar); sinyal mum kapanışında üretilir,
giriş bir sonraki mumun açılışındadır.
"""
from __future__ import annotations

import math
import random
from typing import Any, Optional

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from ..features.indicators import adx, atr, bollinger, ema, rsi
from ..models import Signal
from ..util import TF_MS
from .base import StrategyContext, make_signal

TFS = ("15m", "1h", "4h")
TF_TR = {"15m": "15 dakikalık", "1h": "1 saatlik", "4h": "4 saatlik"}
MIN_STOP = 0.006
MAX_STOP = 0.10

# --- kural uzayı ----------------------------------------------------------------------------
ENTRY_SPACE: dict[str, dict[str, list]] = {
    "donchian": {"n": [20, 40, 60]},
    "ema_cross": {"pair": [[9, 21], [20, 50], [50, 200]]},
    "rsi_revert": {"level": [25, 30, 35]},
    "bb_revert": {"k": [2.0, 2.5]},
    "bb_break": {"k": [2.0, 2.5]},
    "keltner": {"k": [1.5, 2.0, 2.5]},
    "macd": {},
    "roc": {"n": [12, 24, 48]},
    "pullback": {"level": [40, 45]},
    "squeeze": {},
    "volume_break": {"z": [1.5, 2.0, 2.5]},
}
FILTERS = ["trend", "slope", "adx_trend", "adx_range", "volume", "btc", "calm"]
CONFLICTS = {("adx_trend", "adx_range"), ("adx_range", "adx_trend")}
STOPS = [1.0, 1.5, 2.0, 2.5, 3.0]
TP_FIXED = [1.0, 1.5, 2.0, 3.0]
TP_TRAIL = [1.0, 1.5, 2.0]
TRAILS = [2.0, 3.0, 4.0]
TIME_BARS = [12, 24, 48, 96]


def long_period(tf: str) -> int:
    """Uzun trend ortalaması: 4 saatlikte canlı tampon (≈120 mum) sınırı nedeniyle 50."""
    return 50 if tf == "4h" else 200


def _valid_entry(entry: dict, tf: str) -> bool:
    if entry["type"] == "ema_cross" and tf == "4h" and entry["pair"][1] > 50:
        return False
    if entry["type"] == "donchian" and tf == "4h" and entry["n"] > 40:
        return False
    return True


def random_rule(rng: random.Random) -> dict:
    while True:
        tf = rng.choices(TFS, weights=[0.3, 0.4, 0.3])[0]
        et = rng.choice(list(ENTRY_SPACE))
        entry: dict[str, Any] = {"type": et}
        for k, choices in ENTRY_SPACE[et].items():
            entry[k] = rng.choice(choices)
        if _valid_entry(entry, tf):
            break
    nf = rng.choices([0, 1, 2], weights=[0.3, 0.5, 0.2])[0]
    filters: list[str] = []
    while len(filters) < nf:
        f = rng.choice(FILTERS)
        if f not in filters and not any((f, g) in CONFLICTS for g in filters):
            filters.append(f)
    mode = rng.choice(["fixed", "trail"])
    rule = {
        "tf": tf, "entry": entry, "filters": sorted(filters), "stop_atr": rng.choice(STOPS),
        "exit": {"mode": mode, "tp_r": rng.choice(TP_FIXED if mode == "fixed" else TP_TRAIL),
                 "trail_atr": rng.choice(TRAILS) if mode == "trail" else None},
        "time_bars": rng.choice(TIME_BARS),
        "sides": rng.choices(["both", "long", "short"], weights=[0.4, 0.35, 0.25])[0],
    }
    return rule


def mutate(rule: dict, rng: random.Random) -> dict:
    """Komşu kural: 1-2 özelliği değiştirir (evrimsel aramanın ikinci aşaması)."""
    r = {**rule, "entry": dict(rule["entry"]), "exit": dict(rule["exit"]), "filters": list(rule["filters"])}
    for _ in range(rng.choice([1, 1, 2])):
        what = rng.choice(["entry_param", "filters", "stop", "exit", "time", "sides", "entry_param"])
        if what == "entry_param":
            space = ENTRY_SPACE[r["entry"]["type"]]
            if space:
                k = rng.choice(list(space))
                r["entry"][k] = rng.choice(space[k])
        elif what == "filters":
            if r["filters"] and rng.random() < 0.5:
                r["filters"].pop(rng.randrange(len(r["filters"])))
            elif len(r["filters"]) < 2:
                f = rng.choice(FILTERS)
                if f not in r["filters"] and not any((f, g) in CONFLICTS for g in r["filters"]):
                    r["filters"].append(f)
            r["filters"] = sorted(r["filters"])
        elif what == "stop":
            r["stop_atr"] = rng.choice(STOPS)
        elif what == "exit":
            mode = rng.choice(["fixed", "trail"])
            r["exit"] = {"mode": mode, "tp_r": rng.choice(TP_FIXED if mode == "fixed" else TP_TRAIL),
                         "trail_atr": rng.choice(TRAILS) if mode == "trail" else None}
        elif what == "time":
            r["time_bars"] = rng.choice(TIME_BARS)
        else:
            r["sides"] = rng.choice(["both", "long", "short"])
    if not _valid_entry(r["entry"], r["tf"]):
        return dict(rule)
    return r


# --- göstergeler (önbellekli) -----------------------------------------------------------------
def _cross_up(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    out = np.zeros(len(a), dtype=bool)
    if len(a) > 1:
        with np.errstate(invalid="ignore"):
            out[1:] = (a[1:] > b[1:]) & (a[:-1] <= b[:-1])
    return out


def _rolling(x: np.ndarray, n: int, fn: str, shift: int = 1) -> np.ndarray:
    """Önceki n mumun max/min/ortalaması (shift=1: içinde bulunulan mum hariç)."""
    out = np.full(len(x), np.nan)
    if len(x) < n + shift:
        return out
    w = sliding_window_view(x, n)
    v = w.max(axis=1) if fn == "max" else w.min(axis=1) if fn == "min" else w.mean(axis=1)
    out[n - 1 + shift :] = v[: len(v) - shift] if shift else v
    return out


def _vol_z(qv: np.ndarray, n: int = 96) -> np.ndarray:
    x = np.log1p(np.maximum(qv, 0.0))
    out = np.full(len(x), np.nan)
    if len(x) <= n:
        return out
    cs = np.concatenate(([0.0], np.cumsum(x)))
    cs2 = np.concatenate(([0.0], np.cumsum(x * x)))
    # i için önceki n mum: [i-n, i)
    i = np.arange(n, len(x))
    m = (cs[i] - cs[i - n]) / n
    var = np.maximum((cs2[i] - cs2[i - n]) / n - m * m, 1e-12)
    out[n:] = (x[n:] - m) / np.sqrt(var)
    return out


class ByteLRU:
    """Bayt sınırlı LRU önbellek: uzun geçmişte işçi belleğinin şişmesini önler."""

    def __init__(self, max_bytes: int):
        from collections import OrderedDict
        self.max_bytes = max_bytes
        self.used = 0
        self._d: "OrderedDict[tuple, tuple[Any, int]]" = OrderedDict()

    @staticmethod
    def _size(v: Any) -> int:
        if isinstance(v, np.ndarray):
            return v.nbytes
        if isinstance(v, tuple):
            return sum(x.nbytes for x in v if isinstance(x, np.ndarray))
        return 64

    def get(self, k: tuple) -> Any:
        item = self._d.get(k)
        if item is None:
            return None
        self._d.move_to_end(k)
        return item[0]

    def put(self, k: tuple, v: Any) -> None:
        size = self._size(v)
        self._d[k] = (v, size)
        self.used += size
        while self.used > self.max_bytes and len(self._d) > 1:
            _, (_, sz) = self._d.popitem(last=False)
            self.used -= sz


class Ind:
    """Bir sembol + zaman dilimi için gösterge önbelleği (isteğe bağlı paylaşılan LRU ile)."""

    def __init__(self, a: dict[str, np.ndarray], cache: Optional[ByteLRU] = None, prefix: tuple = ()):
        self.a = a
        self.c = np.asarray(a["c"], dtype=float)
        self._m: dict[tuple, np.ndarray] = {}
        self._lru = cache
        self._prefix = prefix

    def get(self, key: tuple) -> Any:
        if self._lru is not None:
            v = self._lru.get(self._prefix + key)
            if v is not None:
                return v
            v = self._compute(key)
            self._lru.put(self._prefix + key, v)
            return v
        if key not in self._m:
            self._m[key] = self._compute(key)
        return self._m[key]

    def _compute(self, key: tuple) -> Any:
        a, c = self.a, self.c
        name = key[0]
        if name == "ema":
            v = ema(c, key[1])
        elif name == "atr":
            v = atr(a["h"], a["l"], c, 14)
        elif name == "rsi":
            v = rsi(c, 14)
        elif name == "adx":
            v = adx(a["h"], a["l"], c, 14)
        elif name == "bb":
            v = bollinger(c, 20, key[1])
        elif name == "hh":
            v = _rolling(np.asarray(a["h"], float), key[1], "max")
        elif name == "ll":
            v = _rolling(np.asarray(a["l"], float), key[1], "min")
        elif name == "volz":
            v = _vol_z(np.asarray(a["qv"], float))
        elif name == "macd":
            m = ema(c, 12) - ema(c, 26)
            m2 = np.where(np.isnan(m), 0.0, m)
            sig = ema(m2, 9)
            v = (m, sig)
        elif name == "roc":
            n = key[1]
            v = np.full(len(c), np.nan)
            if len(c) > n:
                v[n:] = c[n:] / c[:-n] - 1.0
        elif name == "bbw_mean":
            _, up, lo, _ = self.get(("bb", 2.0))
            w = (up - lo) / c
            v = _rolling(np.where(np.isnan(w), 0.0, w), key[1], "mean")
        elif name == "atrp_mean":
            ap = self.get(("atr",)) / c
            v = _rolling(np.where(np.isnan(ap), 0.0, ap), 100, "mean")
        else:
            raise KeyError(key)
        return v


def rule_signals(ind: Ind, rule: dict, btc_trend: Optional[np.ndarray] = None) -> tuple[np.ndarray, np.ndarray]:
    """(long, short) boolean dizileri: i. mum kapanışında sinyal var mı."""
    c = ind.c
    n = len(c)
    tf = rule["tf"]
    e = rule["entry"]
    t = e["type"]
    lp = long_period(tf)
    with np.errstate(invalid="ignore"):
        if t == "donchian":
            hh, ll = ind.get(("hh", e["n"])), ind.get(("ll", e["n"]))
            lo, sh = _cross_up(c, hh), _cross_up(-c, -ll)
        elif t == "ema_cross":
            f, s = ind.get(("ema", e["pair"][0])), ind.get(("ema", e["pair"][1]))
            lo, sh = _cross_up(f, s), _cross_up(s, f)
        elif t == "rsi_revert":
            r = ind.get(("rsi",))
            lv = float(e["level"])
            lo, sh = _cross_up(r, np.full(n, lv)), _cross_up(np.full(n, 100 - lv), r)
        elif t == "bb_revert":
            _, up, lw, _ = ind.get(("bb", e["k"]))
            lo, sh = _cross_up(c, lw), _cross_up(up, c)
        elif t == "bb_break":
            _, up, lw, _ = ind.get(("bb", e["k"]))
            lo, sh = _cross_up(c, up), _cross_up(lw, c)
        elif t == "keltner":
            mid, at = ind.get(("ema", 20)), ind.get(("atr",))
            lo, sh = _cross_up(c, mid + e["k"] * at), _cross_up(mid - e["k"] * at, c)
        elif t == "macd":
            m, sg = ind.get(("macd",))
            lo, sh = _cross_up(m, sg), _cross_up(sg, m)
        elif t == "roc":
            r = ind.get(("roc", e["n"]))
            z = np.zeros(n)
            lo, sh = _cross_up(r, z), _cross_up(z, r)
        elif t == "pullback":
            r = ind.get(("rsi",))
            fast, slow = ind.get(("ema", 20 if tf == "4h" else 50)), ind.get(("ema", lp))
            lv = float(e["level"])
            up_tr, dn_tr = fast > slow, fast < slow
            lo = _cross_up(r, np.full(n, lv)) & up_tr
            sh = _cross_up(np.full(n, 100 - lv), r) & dn_tr
        elif t == "squeeze":
            _, up, lw, _ = ind.get(("bb", 2.0))
            w = (up - lw) / c
            wm = ind.get(("bbw_mean", 40 if tf == "4h" else 100))
            tight = np.zeros(n, dtype=bool)
            tight[1:] = w[:-1] < 0.75 * wm[:-1]
            lo, sh = _cross_up(c, up) & tight, _cross_up(lw, c) & tight
        elif t == "volume_break":
            vz = ind.get(("volz",))
            hh, ll = ind.get(("hh", 10)), ind.get(("ll", 10))
            lo, sh = _cross_up(c, hh) & (vz > e["z"]), _cross_up(-c, -ll) & (vz > e["z"])
        else:
            raise ValueError(t)

        for f in rule["filters"]:
            if f == "trend":
                el = ind.get(("ema", lp))
                lo, sh = lo & (c > el), sh & (c < el)
            elif f == "slope":
                el = ind.get(("ema", 50 if tf == "4h" else 100))
                prev = np.full(n, np.nan)
                prev[20:] = el[:-20]
                lo, sh = lo & (el > prev), sh & (el < prev)
            elif f == "adx_trend":
                d = ind.get(("adx",))
                lo, sh = lo & (d > 20), sh & (d > 20)
            elif f == "adx_range":
                d = ind.get(("adx",))
                lo, sh = lo & (d < 20), sh & (d < 20)
            elif f == "volume":
                vz = ind.get(("volz",))
                lo, sh = lo & (vz > 1.0), sh & (vz > 1.0)
            elif f == "btc":
                if btc_trend is not None:
                    lo, sh = lo & (btc_trend > 0), sh & (btc_trend < 0)
            elif f == "calm":
                ap = ind.get(("atr",)) / c
                apm = ind.get(("atrp_mean",))
                ok = ap < 1.5 * apm
                lo, sh = lo & ok, sh & ok
    if rule["sides"] == "long":
        sh = np.zeros(n, dtype=bool)
    elif rule["sides"] == "short":
        lo = np.zeros(n, dtype=bool)
    return lo, sh


def btc_trend_series(btc_1h: dict[str, np.ndarray]) -> np.ndarray:
    """BTC 1 saatlik trendi: +1 (fiyat > EMA50 > EMA200), −1 (tersi), 0 kararsız."""
    c = np.asarray(btc_1h["c"], float)
    e50, e200 = ema(c, 50), ema(c, 200)
    out = np.zeros(len(c))
    with np.errstate(invalid="ignore"):
        out[(c > e50) & (e50 > e200)] = 1
        out[(c < e50) & (e50 < e200)] = -1
    return out


def stop_for(close: float, atr_v: float, sgn: int, k: float) -> Optional[float]:
    """Ortak stop kuralı (laboratuvar, simülasyon ve canlıda aynı)."""
    if not math.isfinite(atr_v) or atr_v <= 0:
        return None
    stop = close - sgn * k * atr_v
    dist = abs(close - stop) / close
    if dist < MIN_STOP:
        stop = close - sgn * MIN_STOP * close
    elif dist > MAX_STOP:
        return None
    return stop


# --- açıklama ---------------------------------------------------------------------------
ENTRY_TR = {
    "donchian": lambda e: f"fiyat son {e['n']} mumun zirvesini kırınca (dip kırılımında short)",
    "ema_cross": lambda e: f"EMA{e['pair'][0]}, EMA{e['pair'][1]}'yi yukarı kesince (aşağı kesişte short)",
    "rsi_revert": lambda e: f"RSI {e['level']} seviyesini aşırı satımdan yukarı kesince (short: {100 - e['level']} aşağı)",
    "bb_revert": lambda e: f"fiyat Bollinger alt bandının (k={e['k']:g}) dışından içeri dönünce (üst bantta short)",
    "bb_break": lambda e: f"fiyat Bollinger üst bandını (k={e['k']:g}) yukarı kırınca (alt bantta short)",
    "keltner": lambda e: f"fiyat EMA20 + {e['k']:g}×ATR Keltner kanalını kırınca",
    "macd": lambda e: "MACD çizgisi sinyal çizgisini yukarı kesince (aşağı kesişte short)",
    "roc": lambda e: f"{e['n']} mumluk getiri (momentum) sıfırın üstüne geçince",
    "pullback": lambda e: f"yükselen trendde geri çekilme sonrası RSI {e['level']} seviyesini yukarı kesince",
    "squeeze": lambda e: "volatilite sıkışmasının ardından Bollinger bandı kırılınca",
    "volume_break": lambda e: f"hacim z-skoru {e['z']:g}'nin üstündeyken 10 mumluk zirve kırılınca",
}
FILTER_TR = {
    "trend": "fiyat uzun ortalamanın doğru tarafında",
    "slope": "orta vadeli ortalama trend yönünde eğimli",
    "adx_trend": "ADX > 20 (trend var)",
    "adx_range": "ADX < 20 (yatay piyasa)",
    "volume": "hacim ortalamanın üstünde",
    "btc": "BTC trendi aynı yönde",
    "calm": "aşırı oynaklık yok",
}
SIDES_TR = {"both": "long + short", "long": "sadece long", "short": "sadece short"}


def describe_rule(rule: dict) -> dict:
    e = rule["entry"]
    ex = rule["exit"]
    lines = [
        f"Grafik: {TF_TR[rule['tf']]} · Yön: {SIDES_TR[rule['sides']]}",
        "Giriş: " + ENTRY_TR[e["type"]](e),
        "Filtre: " + (", ".join(FILTER_TR[f] for f in rule["filters"]) if rule["filters"] else "yok"),
        f"Stop: {rule['stop_atr']:g}×ATR",
        (f"Çıkış: {ex['tp_r']:g}R hedefte tamamı" if ex["mode"] == "fixed"
         else f"Çıkış: {ex['tp_r']:g}R'de yarısı, kalanı {ex['trail_atr']:g}×ATR iz süren stop"),
        f"Zaman stopu: {rule['time_bars']} mum içinde ilerlemezse",
    ]
    short = {
        "donchian": f"Kanal kırılımı {e.get('n')}", "ema_cross": f"EMA {e.get('pair', [0, 0])[0]}/{e.get('pair', [0, 0])[1]}",
        "rsi_revert": f"RSI dönüş {e.get('level')}", "bb_revert": "Bollinger dönüş", "bb_break": "Bollinger kırılım",
        "keltner": "Keltner kırılım", "macd": "MACD kesişim", "roc": f"Momentum {e.get('n')}",
        "pullback": "Trendde geri çekilme", "squeeze": "Sıkışma kırılımı", "volume_break": "Hacimli kırılım",
    }[e["type"]]
    exit_short = f"{ex['tp_r']:g}R" if ex["mode"] == "fixed" else f"{ex['tp_r']:g}R+iz"
    side_short = {"both": "", "long": " · long", "short": " · short"}[rule["sides"]]
    label = f"{short} · {rule['tf']}" + (f" · {'+'.join(rule['filters'])}" if rule["filters"] else "") + f" · {exit_short}{side_short}"
    return {"label": label, "details": lines}


# --- canlı / simülasyon stratejisi ---------------------------------------------------------
GROUP_TR = {"all": "Tüm coinler", "big": "Büyük coinler", "mid": "Orta oynaklıklı altcoinler", "high": "Yüksek oynaklıklı coinler"}


def hourly_vol(c1h: np.ndarray) -> float:
    """1 saatlik log getirilerin standart sapması (coin gruplamasında kullanılan oynaklık ölçüsü)."""
    c = np.asarray(c1h, float)
    c = c[np.isfinite(c) & (c > 0)]
    if len(c) < 30:
        return float("nan")
    return float(np.std(np.diff(np.log(c))))


def group_for(symbol: str, groups: list[dict], vol: Optional[float] = None) -> Optional[dict]:
    """Sembolün grubu: önce açık listeler, yoksa oynaklık eşiğine göre (büyükler yalnızca listeden)."""
    for g in groups:
        if symbol in g.get("symbols", []):
            return g
    if vol is None or not math.isfinite(vol):
        return None
    for g in groups:
        if g.get("key") in ("big",):
            continue
        lo, hi = g.get("vol_min"), g.get("vol_max")
        if (lo is None or vol > lo) and (hi is None or vol <= hi):
            return g
    return None


def rule_due(rule: dict, scan_tf: str, now_ms_: int) -> bool:
    """Bu tarama anında kuralın zaman dilimi kapanışı mı? (4 saatlik: 1 saatlik taramada 4s sınırı)."""
    ltf = rule.get("tf")
    if ltf == scan_tf:
        return True
    return ltf == "4h" and scan_tf == "1h" and (now_ms_ % TF_MS["4h"]) < 10 * 60_000


class LabRule:
    code = "S6"
    name = "Lab"

    def rule_for(self, symbol: str, ctx: StrategyContext) -> tuple[Optional[dict], Optional[dict]]:
        """(kural, grup) — gruplu kurulumda sembolün grubunun kuralı, yoksa tek kural."""
        st = ctx.settings
        groups = getattr(st, "lab_groups", None)
        if groups:
            g = group_for(symbol, groups)
            if g is None:
                b = ctx.arrays(symbol, "1h", 120)
                g = group_for(symbol, groups, hourly_vol(b["c"][-200:]) if b is not None else None)
            return (g["rule"], g) if g else (None, None)
        rule = getattr(st, "lab_rule", None)
        return (rule, None) if rule else (None, None)

    def evaluate(self, symbol: str, ctx: StrategyContext, rule: Optional[dict] = None, group: Optional[dict] = None) -> Optional[Signal]:
        if rule is None:
            rule, group = self.rule_for(symbol, ctx)
        if not rule:
            return None
        tf = rule["tf"]
        if tf == "4h":
            a1 = ctx.arrays(symbol, "1h", 240)
            if a1 is None:
                return None
            from ..features.indicators import aggregate_bars
            a = aggregate_bars(a1, TF_MS["4h"])
            if len(a["c"]) < 60:
                return None
        else:
            a = ctx.arrays(symbol, tf, 250)
            if a is None:
                return None
        ind = Ind(a)
        btc = None
        if "btc" in rule["filters"]:
            b = ctx.arrays("BTCUSDT", "1h", 210)
            if b is None:
                return None
            v = float(btc_trend_series(b)[-1])
            btc = np.full(len(ind.c), v)
        lo, sh = rule_signals(ind, rule, btc)
        if lo[-1]:
            side, sgn = "LONG", 1
        elif sh[-1]:
            side, sgn = "SHORT", -1
        else:
            return None
        close = float(ind.c[-1])
        at = float(ind.get(("atr",))[-1])
        stop = stop_for(close, at, sgn, float(rule["stop_atr"]))
        if stop is None:
            return None
        ex = rule["exit"]
        if ex["mode"] == "fixed":
            tp_spec, trail = [(float(ex["tp_r"]), 1.0)], None
        else:
            tp_spec, trail = [(float(ex["tp_r"]), 0.5)], float(ex["trail_atr"])
        tf_mult = 4 if tf == "4h" else 1
        sig_tf = "1h" if tf == "4h" else tf
        if trail is not None and tf == "4h":
            # yönetim 1 saatlik mumlarla: aynı mesafe için çarpanı ATR oranıyla ölçekle
            a1 = ctx.arrays(symbol, "1h", 30)
            at1 = float(atr(a1["h"], a1["l"], a1["c"], 14)[-1]) if a1 is not None else float("nan")
            trail = trail * at / at1 if math.isfinite(at1) and at1 > 0 else trail * 2.0
        d = describe_rule(rule)
        paper_only = bool((group or {}).get("paper_only") if group else getattr(ctx.settings, "lab_paper_only", False))
        gname = GROUP_TR.get((group or {}).get("key", "all"), "")
        reasons = ["Strateji Laboratuvarı'nda binlerce kural arasından seçilip görülmemiş veride doğrulanan kural"]
        if group:
            reasons.append(f"Coin grubu: {gname} — bu gruba özel bulunan kural")
        warnings = ["Deneme kuralı: yalnızca Simülasyon'da otomatik işlenir (kanıt toplanıyor)"] if paper_only else []
        feats = {"atr_pct": at / close * 100.0}
        if paper_only:
            feats["paper_only"] = 1.0
        return make_signal(
            symbol=symbol, side=side, strategy=self.code, strategy_name=f"Lab: {d['label']}", timeframe=sig_tf,
            bar_time=int(a["t"][-1]), now=ctx.now, ttl_ms=max(ctx.scanner.signal_ttl_min, 30 * tf_mult) * 60_000,
            entry=close, stop=stop, tp_spec=tp_spec, atr_value=at, score=70.0,
            reasons=reasons + d["details"], warnings=warnings, features=feats,
            trailing_atr_mult=round(trail, 2) if trail else None, time_stop_bars=int(rule["time_bars"]) * tf_mult,
        )
