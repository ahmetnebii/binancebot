"""Ham simülasyon çıktısından BacktestResult (docs/API.md) üretir."""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from . import metrics as M

TRADES_LIMIT = 2000


def _fin(x: Optional[float]) -> Optional[float]:
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def build_result(raw: dict, *, initial_equity: float, start_ms: int, end_ms: int, source: str, extra_warnings: list[str]) -> dict:
    trades: list[dict] = raw["trades"]
    eq_t: np.ndarray = raw["eq_t"]
    eq_v: np.ndarray = raw["eq_v"]
    if len(eq_v) == 0:
        eq_t = np.array([start_ms // 1000, end_ms // 1000], dtype=np.int64)
        eq_v = np.array([initial_equity, initial_equity], dtype=float)
    final_equity = float(eq_v[-1])
    dd = M.drawdown_series(eq_v)
    max_dd = float(-dd.min()) if len(dd) else 0.0
    days = max(1e-9, (end_ms - start_ms) / 86_400_000)
    total_ret = (final_equity / initial_equity - 1.0) * 100.0 if initial_equity > 0 else 0.0
    cagr = None
    if days >= 30 and final_equity > 0 and initial_equity > 0:
        cagr = ((final_equity / initial_equity) ** (365.0 / days) - 1.0) * 100.0
    sharpe, sortino = M.sharpe_sortino(M.daily_returns(eq_t, eq_v))
    rs = [t["r"] for t in trades]
    pnls = [t["pnl"] for t in trades]
    st = M.trade_stats(rs, pnls)
    holds = [(t["exit_time"] - t["entry_time"]) / 3_600_000 for t in trades]

    summary = {
        "trades": st["trades"],
        "win_rate": st["win_rate"],
        "profit_factor": _fin(st["profit_factor"]),
        "expectancy_r": _fin(st["expectancy_r"]),
        "total_return_pct": total_ret,
        "max_drawdown_pct": max_dd,
        "sharpe": sharpe,
        "period_start": start_ms,
        "period_end": end_ms,
        "symbols": len(raw.get("symbols_used", [])),
        "cagr_pct": _fin(cagr),
        "sortino": sortino,
        "calmar": _fin(cagr / max_dd) if cagr is not None and max_dd > 0 else None,
        "avg_win_r": _fin(st["avg_win_r"]),
        "avg_loss_r": _fin(st["avg_loss_r"]),
        "best_r": _fin(st["best_r"]),
        "worst_r": _fin(st["worst_r"]),
        "avg_hold_hours": (sum(holds) / len(holds)) if holds else None,
        "exposure_pct": float(raw.get("exposure_pct", 0.0)),
        "trades_per_day": st["trades"] / days,
        "fees_total": float(raw.get("fees_total", 0.0)),
        "funding_total": float(raw.get("funding_total", 0.0)),
        "initial_equity": initial_equity,
        "final_equity": final_equity,
    }

    by_strategy = []
    for code in sorted({t["strategy"] for t in trades}):
        tt = [t for t in trades if t["strategy"] == code]
        s = M.trade_stats([t["r"] for t in tt], [t["pnl"] for t in tt])
        by_strategy.append({
            "strategy": code, "strategy_name": tt[0]["strategy_name"], "trades": s["trades"],
            "win_rate": s["win_rate"], "profit_factor": _fin(s["profit_factor"]), "expectancy_r": _fin(s["expectancy_r"]),
            "total_pnl": s["total_pnl"], "avg_win_r": _fin(s["avg_win_r"]), "avg_loss_r": _fin(s["avg_loss_r"]),
        })
    by_side = []
    for side in ("LONG", "SHORT"):
        tt = [t for t in trades if t["side"] == side]
        s = M.trade_stats([t["r"] for t in tt], [t["pnl"] for t in tt])
        by_side.append({"side": side, "trades": s["trades"], "win_rate": s["win_rate"], "expectancy_r": _fin(s["expectancy_r"]), "total_pnl": s["total_pnl"]})

    ts, ev, ddv = M.downsample(eq_t, eq_v, dd, max_points=1500)
    equity = [{"time": int(a), "equity": float(b), "drawdown_pct": float(c)} for a, b, c in zip(ts, ev, ddv)]

    risk_fracs = [t.get("risk_frac", 0.0) for t in trades if t.get("risk_frac")]
    mc = M.monte_carlo(rs, float(np.mean(risk_fracs)) if risk_fracs else 0.0, initial_equity)
    folds = 4 if days >= 60 else 3 if days >= 30 else 2
    wf = M.walk_forward(trades, eq_t, eq_v, start_ms, end_ms, folds)

    calibration = {}
    for row in by_strategy:
        if row["trades"] >= 10 and row["win_rate"] is not None:
            calibration[row["strategy"]] = {
                "n": row["trades"],
                "win_rate": row["win_rate"],
                "avg_win_r": row["avg_win_r"] or 0.0,
                "avg_loss_r": abs(row["avg_loss_r"] or 1.0),
                "expectancy_r": row["expectancy_r"] or 0.0,
            }

    warnings = list(extra_warnings) + list(raw.get("warnings", []))
    if st["trades"] < 30:
        warnings.append(f"İşlem sayısı az ({st['trades']}): sonuçlar istatistiksel olarak güvenilir değil; süreyi veya sembol sayısını artırın.")
    sk = raw.get("skipped", {})
    if sum(sk.values()):
        warnings.append(
            f"Alınmayan sinyaller: {sk.get('ev', 0)} düşük beklenti, {sk.get('limits', 0)} risk limiti, "
            f"{sk.get('drift', 0)} fiyat kaçtı, {sk.get('gap', 0)} açılışta stop'u geçti."
        )
    warnings.append("Aynı mumda hem stop hem hedef görüldüğünde önce stop varsayıldı (kötümser yaklaşım).")

    out_trades = [
        {k: t[k] for k in ("symbol", "side", "strategy", "entry_time", "exit_time", "entry_price", "exit_price", "r", "pnl", "fees", "reason", "hold_bars")}
        for t in sorted(trades, key=lambda x: x["exit_time"])[-TRADES_LIMIT:]
    ]
    return {
        "summary": summary,
        "by_strategy": by_strategy,
        "by_side": by_side,
        "equity": equity,
        "monthly": M.monthly_returns(eq_t, eq_v),
        "r_histogram": M.r_histogram(rs),
        "monte_carlo": mc,
        "walk_forward": wf,
        "trades": out_trades,
        "calibration": calibration,
        "data_source": source,
        "warnings": warnings,
    }


SUMMARY_KEYS = ("trades", "win_rate", "profit_factor", "expectancy_r", "total_return_pct", "max_drawdown_pct", "sharpe", "period_start", "period_end", "symbols")


def short_summary(result: dict) -> dict:
    s = result["summary"]
    return {k: s.get(k) for k in SUMMARY_KEYS}
