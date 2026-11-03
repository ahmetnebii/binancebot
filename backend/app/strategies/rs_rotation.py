"""S2 — Göreli Güç Rotasyonu (BTC'den arındırılmış, oynaklığa göre ölçeklenmiş momentum)."""
from __future__ import annotations

import math

from ..features.indicators import atr, beta, ema, log_returns
from ..models import Signal
from .base import StrategyContext, clamp01, make_signal


class RSRotation:
    code = "S2"
    name = "Göreli Güç Rotasyonu"
    timeframe = "1h"

    def evaluate_universe(self, symbols: list[str], ctx: StrategyContext) -> list[Signal]:
        reg = ctx.regime.name
        if reg in ("CHAOS", "UNKNOWN"):
            return []
        btc = ctx.arrays("BTCUSDT", "1h", 100)
        if btc is None:
            return []
        bc = btc["c"]
        br = log_returns(bc[-73:])
        b24 = bc[-1] / bc[-25] - 1.0
        b72 = bc[-1] / bc[-73] - 1.0
        rows: list[tuple[float, str, dict, float, float]] = []
        for s in symbols:
            if s == "BTCUSDT":
                continue
            a = ctx.arrays(s, "1h", 100)
            if a is None:
                continue
            c = a["c"]
            r = log_returns(c[-73:])
            if len(r) < 72:
                continue
            vol = float(r.std() * math.sqrt(24))
            if vol <= 0:
                continue
            be = beta(r, br)
            al24 = (c[-1] / c[-25] - 1.0) - be * b24
            al72 = (c[-1] / c[-73] - 1.0) - be * b72
            mom = (0.6 * al24 + 0.4 * al72 / 3.0) / vol
            rows.append((float(mom), s, a, float(al24), float(al72)))
        if len(rows) < 6:
            return []
        rows.sort(key=lambda x: x[0], reverse=True)
        n = ctx.settings.s2_top_n
        picks: list[tuple[int, tuple, str]] = []
        if reg == "BULL":
            picks = [(i + 1, r, "LONG") for i, r in enumerate(rows[:n]) if r[0] > 0.3]
        elif reg == "BEAR":
            picks = [(i + 1, r, "SHORT") for i, r in enumerate(reversed(rows[-n:])) if r[0] < -0.3]
        else:
            k = max(1, n - 1)
            picks = [(i + 1, r, "LONG") for i, r in enumerate(rows[:k]) if r[0] > 0.6]
            picks += [(i + 1, r, "SHORT") for i, r in enumerate(reversed(rows[-k:])) if r[0] < -0.6]

        out: list[Signal] = []
        total = len(rows)
        for rank, (mom, sym, a, al24, al72), side in picks:
            c, h, l = a["c"], a["h"], a["l"]  # noqa: E741
            close = float(c[-1])
            e50 = float(ema(c, 50)[-1])
            sgn = 1 if side == "LONG" else -1
            if math.isnan(e50) or sgn * (close - e50) <= 0:
                continue
            at = float(atr(h, l, c, 14)[-1])
            if not math.isfinite(at) or at <= 0:
                continue
            stop = close - sgn * 2.5 * at
            dist = abs(close - stop) / close
            if dist > 0.08:
                continue
            if dist < 0.01:
                stop = close - sgn * 0.01 * close
            fr = ctx.funding_rate(sym)
            aligned = (reg == "BULL" and side == "LONG") or (reg == "BEAR" and side == "SHORT")
            score = 50 + clamp01(abs(mom) / 3.0) * 25 + (10 if aligned else 4) + (8 if rank == 1 else 4 if rank == 2 else 0)
            score -= clamp01(sgn * fr / 0.0005) * 10
            pos_word = "üstünde" if side == "LONG" else "altında"
            reasons = [
                f"Göreli güç sıralaması: {rank}. ({'en güçlü' if side == 'LONG' else 'en zayıf'} {total} sembol içinde)",
                f"BTC'den arındırılmış getiri: 24 saat %{al24 * 100:+.1f}, 72 saat %{al72 * 100:+.1f}",
                f"Piyasa rejimi: {ctx.regime.label}",
                f"Fiyat 1 saatlik EMA50'nin {pos_word}",
            ]
            warnings = []
            if sgn * fr > 0.0003:
                warnings.append("Funding aleyhte: pozisyon taşıma maliyeti yüksek")
            out.append(
                make_signal(
                    symbol=sym, side=side, strategy=self.code, strategy_name=self.name, timeframe=self.timeframe,
                    bar_time=int(a["t"][-1]), now=ctx.now, ttl_ms=max(ctx.scanner.signal_ttl_min, 45) * 60_000,
                    entry=close, stop=stop, tp_spec=[(2.0, 0.5)], atr_value=at, score=score, reasons=reasons,
                    warnings=warnings,
                    features={"momentum": mom, "alpha_24h": al24, "alpha_72h": al72, "rank": float(rank), "funding": fr, "atr_pct": at / close * 100.0},
                    trailing_atr_mult=3.0, time_stop_bars=24,
                )
            )
        return out
