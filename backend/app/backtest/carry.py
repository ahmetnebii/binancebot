"""Funding arbitrajı (delta-nötr "cash & carry") analizi.

Fikir: spotta X miktar coin al, vadelide aynı miktarda short aç. Fiyat yönü birbirini götürür;
pozisyon, vadeli tarafta ödenen funding'i toplar (funding pozitifken short taraf alır).

Bu modül YALNIZCA geçmiş funding oranlarıyla, gelecek bilgisi kullanmadan şunu ölçer:
- Coin başına: "son k funding ortalaması eşiğin üstündeyse pozisyonda kal" kuralı ne kazandırırdı?
- Portföy: her funding döneminde, geçmiş funding'i en yüksek ilk N coine eşit sermaye.
Kural ve parametreler EĞİTİM döneminde (%60) seçilir, sonuç hiç görülmemiş TEST döneminde (%40) raporlanır.

Maliyetler: her giriş/çıkışta spot ve vadeli komisyon + kayma (iki bacak). Sermaye: spot bacak tam
nominal, vadeli bacak 1/kaldıraç teminat → getiri sermayeye (1 + 1/kaldıraç) bölünür.

Modellenmeyenler (raporda uyarılır): spot–vadeli fiyat farkı (baz) dalgalanması, borsa riski,
short bacağın ani yükselişte likidasyon riski (teminat yönetimi gerekir), coinin spotta olmaması.
"""
from __future__ import annotations

import itertools
import math
from typing import Optional

import numpy as np

from .data import HistoryData

WINDOW_MS = 8 * 3_600_000
YEAR_MS = 365 * 86_400_000
PER_YEAR = YEAR_MS / WINDOW_MS  # 1095 sekiz saatlik dönem

K_CHOICES = [3, 9, 21, 42]          # son kaç dönemin ortalaması (1 gün … 2 hafta)
ENTER_APR = [5.0, 10.0, 15.0, 25.0, 40.0]
EXIT_APR = [0.0, 5.0]
TOP_N = [3, 5, 8]


def _windows(start: int, end: int) -> np.ndarray:
    s = start - start % WINDOW_MS
    return np.arange(s, end, WINDOW_MS, dtype=np.int64)


def funding_matrix(data: HistoryData, symbols: list[str], start: int, end: int) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """R[coin, pencere] = o 8 saatlik pencerede ödenen funding toplamı (4 saatlik coinlerde iki ödeme).
    Coin o tarihte listelenmemişse NaN."""
    w = _windows(start, end)
    syms = [s for s in symbols if s in data.funding and len(data.funding[s][0])]
    R = np.full((len(syms), len(w)), np.nan)
    for i, s in enumerate(syms):
        t, r = data.funding[s]
        idx = np.searchsorted(w, t, side="right") - 1  # ödemenin düştüğü pencere
        ok = (idx >= 0) & (idx < len(w)) & (t < end)
        row = np.zeros(len(w))
        np.add.at(row, idx[ok], r[ok])
        first = int(idx[ok].min()) if ok.any() else len(w)
        row[:first] = np.nan
        R[i] = row
    return w, R, syms


def _trailing_mean(row: np.ndarray, k: int) -> np.ndarray:
    """j. pencereye karar verirken bilinen son k pencerenin ortalaması (j-k … j-1). İlk k pencerede NaN."""
    n = len(row)
    out = np.full(n, np.nan)
    x = np.where(np.isnan(row), 0.0, row)
    valid = (~np.isnan(row)).astype(float)
    cs = np.concatenate(([0.0], np.cumsum(x)))
    cv = np.concatenate(([0.0], np.cumsum(valid)))
    j = np.arange(k, n)
    cnt = cv[j] - cv[j - k]
    m = (cs[j] - cs[j - k]) / np.maximum(cnt, 1)
    m[cnt < k] = np.nan
    out[k:] = m
    return out


def _metrics(ret: np.ndarray, held: np.ndarray, w: np.ndarray) -> dict:
    """ret: pencere başına sermaye getirisi (kesir). Basit (bileşiksiz) toplam."""
    n = len(ret)
    if n == 0:
        return {"return_pct": 0.0, "apr_pct": 0.0, "max_drawdown_pct": 0.0, "time_in_pct": 0.0, "windows": 0,
                "worst_window_pct": 0.0, "positive_months_pct": None, "months": 0}
    cum = np.cumsum(ret)
    peak = np.maximum.accumulate(np.r_[0.0, cum])[1:]
    dd = float(np.max(peak - cum)) if n else 0.0
    years = n / PER_YEAR
    months: dict[str, float] = {}
    for t, r in zip(w, ret):
        key = str(np.datetime_as_string(np.datetime64(int(t), "ms"), unit="M"))
        months[key] = months.get(key, 0.0) + float(r)
    pos_m = sum(1 for v in months.values() if v > 0)
    return {
        "return_pct": float(cum[-1]) * 100.0,
        "apr_pct": float(cum[-1]) / years * 100.0 if years > 0 else 0.0,
        "max_drawdown_pct": dd * 100.0,
        "time_in_pct": float(np.mean(held)) * 100.0 if len(held) else 0.0,
        "windows": int(n),
        "worst_window_pct": float(np.min(ret)) * 100.0,
        "positive_months_pct": (pos_m / len(months) * 100.0) if months else None,
        "months": len(months),
    }


def _monthly(ret: np.ndarray, w: np.ndarray) -> list[dict]:
    months: dict[str, float] = {}
    for t, r in zip(w, ret):
        key = str(np.datetime_as_string(np.datetime64(int(t), "ms"), unit="M"))
        months[key] = months.get(key, 0.0) + float(r)
    return [{"month": k, "return_pct": v * 100.0} for k, v in sorted(months.items())]


def run_single(row: np.ndarray, k: int, enter_apr: float, exit_apr: float, cost_rt: float, eff: float) -> tuple[np.ndarray, np.ndarray]:
    """Tek coin kuralı. Dönüş: (pencere başına sermaye getirisi, pozisyonda mı)."""
    tm = _trailing_mean(row, k) * PER_YEAR * 100.0  # yıllık % (karar anında bilinen)
    n = len(row)
    ret = np.zeros(n)
    held = np.zeros(n, dtype=bool)
    pos = False
    half = cost_rt / 2.0
    for j in range(n):
        if np.isnan(row[j]):
            if pos:
                ret[j] -= half * eff
                pos = False
            continue
        m = tm[j]
        if not pos and not np.isnan(m) and m > enter_apr:
            pos = True
            ret[j] -= half * eff
        elif pos and (np.isnan(m) or m < exit_apr):
            pos = False
            ret[j] -= half * eff
        if pos:
            ret[j] += row[j] * eff
            held[j] = True
    return ret, held


def portfolio_step(trailing_apr: dict, holdings: set, top_n: int, enter_apr: float, exit_apr: float) -> set:
    """Bir funding döneminde tutulacak coinler (canlı sanal portföy ve geçmiş test AYNI kuralı kullanır).
    trailing_apr: coin → karar anında bilinen geçmiş ortalaması (yıllık %), yalnızca işlem gören coinler."""
    order = sorted(trailing_apr, key=lambda c: -trailing_apr[c])
    rank = {c: r for r, c in enumerate(order)}
    new = {c for c in holdings if c in rank and trailing_apr[c] >= exit_apr and rank[c] < 2 * top_n}
    for c in order:
        if len(new) >= top_n:
            break
        if trailing_apr[c] > enter_apr:
            new.add(c)
    return new


def run_portfolio(R: np.ndarray, k: int, enter_apr: float, exit_apr: float, top_n: int, cost_rt: float,
                  eff: float) -> tuple[np.ndarray, np.ndarray, list[set[int]]]:
    """Her pencerede: geçmiş ortalaması en yüksek ilk N coin (eşik üstü), eşit sermaye dilimleri.
    Tutulan coin, eşiğin üstünde ve ilk 2N içinde kaldıkça bırakılmaz (gereksiz al-sat maliyetini önler)."""
    n_coin, n = R.shape
    TM = np.vstack([_trailing_mean(R[i], k) for i in range(n_coin)]) * PER_YEAR * 100.0 if n_coin else np.zeros((0, n))
    ret = np.zeros(n)
    held_frac = np.zeros(n)
    holdings: set[int] = set()
    history: list[set[int]] = []
    half = cost_rt / 2.0
    slot = 1.0 / top_n
    for j in range(n):
        col = TM[:, j]
        live = ~np.isnan(R[:, j])
        trailing = {int(i): float(col[i]) for i in range(n_coin) if live[i] and not np.isnan(col[i])}
        new = portfolio_step(trailing, holdings, top_n, enter_apr, exit_apr)
        changes = len(new ^ holdings)
        ret[j] -= changes * slot * half * eff
        for c in new:
            ret[j] += slot * R[c, j] * eff
        holdings = new
        held_frac[j] = len(new) / top_n
        history.append(set(new))
    return ret, held_frac, history


def portfolio_trades(R: np.ndarray, w: np.ndarray, syms: list[str], hist: list[set[int]], top_n: int,
                     cost_rt: float, eff: float, from_idx: int = 0) -> list[dict]:
    """Portföyün coin bazında giriş/çıkış kayıtları ("işlemler"). from_idx: yalnızca bu pencereden sonra başlayanlar.
    funding_pct / net_pct: o coine ayrılan sermaye diliminin getirisi (%), net = funding − giriş/çıkış maliyeti."""
    out: list[dict] = []
    open_at: dict[int, int] = {}
    prev: set[int] = set()
    n = len(hist)

    def close(c: int, j_end: int, still_open: bool) -> None:
        j0 = open_at.pop(c)
        if j0 < from_idx:
            return
        fund = float(np.nansum(R[c, j0:j_end])) * eff
        cost = cost_rt * eff * (0.5 if still_open else 1.0)
        out.append({
            "symbol": syms[c], "entry_time": int(w[j0]), "exit_time": int(w[j_end]) if j_end < len(w) else int(w[-1] + WINDOW_MS),
            "windows": j_end - j0, "hours": (j_end - j0) * 8, "funding_pct": fund * 100.0, "cost_pct": cost * 100.0,
            "net_pct": (fund - cost) * 100.0, "open": still_open,
        })

    for j in range(n):
        cur = hist[j]
        for c in cur - prev:
            open_at[c] = j
        for c in prev - cur:
            close(c, j, False)
        prev = cur
    for c in list(open_at):
        close(c, n, True)
    out.sort(key=lambda t: t["entry_time"])
    return out


def analyze(data: HistoryData, symbols: list[str], start: int, end: int, *, spot_fee: float = 0.001,
            perp_fee: float = 0.0005, slippage: float = 0.0002, leverage: float = 3.0,
            progress=None) -> dict:
    """Eğitim (%60) / test (%40) ayrımlı funding arbitrajı analizi."""
    w, R, syms = funding_matrix(data, symbols, start, end)
    if not syms or len(w) < 60:
        raise ValueError("Funding arbitrajı analizi için yeterli funding geçmişi yok; önce verileri indirin")
    split = int(len(w) * 0.6)
    cost_rt = 2.0 * (spot_fee + perp_fee + 2.0 * slippage)
    eff = 1.0 / (1.0 + 1.0 / leverage)

    def seg(a: np.ndarray, part: str) -> np.ndarray:
        return a[:split] if part == "train" else a[split:]

    # --- coin başına ----------------------------------------------------------------
    coins = []
    grid = [(k, e, x) for k, e, x in itertools.product(K_CHOICES, ENTER_APR, EXIT_APR) if x < e]
    for i, s in enumerate(syms):
        row = R[i]
        valid = ~np.isnan(row)
        if valid.sum() < 60:
            continue
        best = None
        for k, e, x in grid:
            ret, held = run_single(row, k, e, x, cost_rt, eff)
            tr = float(np.sum(seg(ret, "train")))
            if best is None or tr > best[0]:
                best = (tr, k, e, x, ret, held)
        _, k, e, x, ret, held = best
        always = np.where(np.isnan(row), 0.0, row) * eff
        r_all = row[valid]
        coins.append({
            "symbol": s,
            "avg_apr_pct": float(np.mean(r_all)) * PER_YEAR * 100.0,
            "positive_pct": float(np.mean(r_all > 0)) * 100.0,
            "recent_apr_pct": float(np.nanmean(row[-21:])) * PER_YEAR * 100.0 if valid[-21:].any() else None,
            "rule": {"k": k, "enter_apr": e, "exit_apr": x},
            "train": _metrics(seg(ret, "train"), seg(held, "train"), seg(w, "train")),
            "test": _metrics(seg(ret, "test"), seg(held, "test"), seg(w, "test")),
            "always_test": _metrics(seg(always, "test"), np.ones(len(w) - split), seg(w, "test")),
        })
        if progress:
            progress(0.6 * (i + 1) / len(syms), f"{s} funding geçmişi analiz edildi")

    # --- portföy ----------------------------------------------------------------------
    pgrid = [(k, e, x, n) for k, e, x, n in itertools.product(K_CHOICES, ENTER_APR, EXIT_APR, TOP_N) if x < e]
    best_p = None
    for gi, (k, e, x, n) in enumerate(pgrid):
        ret, held, _ = run_portfolio(R, k, e, x, n, cost_rt, eff)
        tr = float(np.sum(seg(ret, "train")))
        dd = _metrics(seg(ret, "train"), seg(held, "train"), seg(w, "train"))["max_drawdown_pct"]
        score = tr * 100.0 - 0.5 * dd  # düşüşü de cezalandır
        if best_p is None or score > best_p[0]:
            best_p = (score, k, e, x, n)
        if progress and gi % 10 == 0:
            progress(0.6 + 0.35 * (gi + 1) / len(pgrid), "Portföy parametreleri eğitim döneminde seçiliyor")
    _, k, e, x, n = best_p
    ret, held, hist = run_portfolio(R, k, e, x, n, cost_rt, eff)
    trades_test = portfolio_trades(R, w, syms, hist, n, cost_rt, eff, from_idx=split)
    cum = np.cumsum(ret)
    peak = np.maximum.accumulate(np.r_[0.0, cum])[1:]
    initial = 1000.0
    step = max(1, len(w) // 1500)
    equity = [
        {"time": int(w[j] // 1000), "equity": float(initial * (1 + cum[j])),
         "drawdown_pct": float(-(peak[j] - cum[j]) / (1 + peak[j]) * 100.0)}
        for j in range(0, len(w), step)
    ]
    # bugün (son veriyle) bu kurallar hangi coinleri tutardı?
    TM_now = np.array([_trailing_mean(R[i], k)[-1] if len(R[i]) else np.nan for i in range(len(syms))]) * PER_YEAR * 100.0
    now = sorted(
        [{"symbol": syms[i], "trailing_apr_pct": float(TM_now[i]), "held": i in hist[-1]} for i in range(len(syms)) if not np.isnan(TM_now[i])],
        key=lambda d: -d["trailing_apr_pct"],
    )
    portfolio = {
        "params": {"k": k, "enter_apr": e, "exit_apr": x, "top_n": n},
        "train": _metrics(seg(ret, "train"), seg(held, "train"), seg(w, "train")),
        "test": _metrics(seg(ret, "test"), seg(held, "test"), seg(w, "test")),
        "equity": equity,
        "monthly_test": _monthly(seg(ret, "test"), seg(w, "test")),
        "trades_test": trades_test[-1000:],
        "now": now[:15],
    }
    t = portfolio["test"]
    if t["windows"] and t["apr_pct"] >= 8 and t["max_drawdown_pct"] <= 5 and (t["positive_months_pct"] or 0) >= 70:
        verdict = "recommended"
    elif t["windows"] and t["apr_pct"] > 3:
        verdict = "weak"
    else:
        verdict = "none"
    coins.sort(key=lambda c: -c["test"]["apr_pct"])
    if progress:
        progress(1.0, "Funding analizi tamamlandı")
    return {
        "verdict": verdict,
        "period": {"start": int(w[0]), "split": int(w[split]), "end": int(end)},
        "costs": {"spot_fee": spot_fee, "perp_fee": perp_fee, "slippage": slippage, "round_trip_pct": cost_rt * 100.0,
                  "leverage": leverage, "capital_efficiency": eff},
        "portfolio": portfolio,
        "coins": coins,
    }


def trailing_apr(rates: list[float], k: int, per_year: Optional[float] = None) -> Optional[float]:
    """Canlı radar için: son k funding ödemesinin yıllıklandırılmış ortalaması (%)."""
    if len(rates) < k:
        return None
    return float(np.mean(rates[-k:])) * (per_year or PER_YEAR) * 100.0


def apr_of(rate: float, interval_hours: float = 8.0) -> float:
    return rate * (365 * 24 / interval_hours) * 100.0


def isfinite(x: Optional[float]) -> bool:
    return x is not None and math.isfinite(x)
