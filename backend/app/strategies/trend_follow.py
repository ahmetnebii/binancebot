"""S4 — 4 Saatlik Trend Takibi (zaman serisi momentumu).

Kriptoda en sağlam belgelenmiş etkilerden biri: orta vadeli trendlerin sürme eğilimi.
15 dakikalık kırılımlardan farklı olarak seyrek işlem açar; komisyon/kayma payı küçüktür
ve kazançlar iz süren stopla uzun trendlerde büyür.

Kurallar (4 saatlik mumlar, 1 saatlik tampondan türetilir):
- Kapanış son N mumun en yükseğinin üstünde (long) / en düşüğünün altında (short)
- EMA20 > EMA50 ve fiyat > EMA50 (long; short simetrik)
- 7 günlük getiri trend yönünde
- Stop: kırılım mumundan k × ATR(4s); 4 saatlik kapanışlarda değerlendirilir
"""
from __future__ import annotations

import math
from typing import Optional

from ..features.indicators import adx, aggregate_bars, atr, ema
from ..models import Signal
from ..util import TF_MS
from .base import StrategyContext, clamp01, make_signal

H4 = TF_MS["4h"]


def is_4h_close(now_ms: int) -> bool:
    """Tarama anı bir 4 saatlik mum kapanışına denk geliyor mu (birkaç dakikalık tolerans)."""
    return (now_ms % H4) < 10 * 60_000


class TrendFollow:
    code = "S4"
    name = "4s Trend Takibi"
    timeframe = "1h"  # yönetim (iz süren stop, zaman stopu) 1 saatlik mumlarla yapılır

    def evaluate(self, symbol: str, ctx: StrategyContext) -> Optional[Signal]:
        cfg = ctx.settings
        a = ctx.arrays(symbol, "1h", 240)
        if a is None:
            return None
        a4 = aggregate_bars(a, H4)
        n = int(cfg.s4_donchian)
        c, h, l = a4["c"], a4["h"], a4["l"]  # noqa: E741
        if len(c) < max(n + 2, 55):
            return None
        close = float(c[-1])
        hh = float(h[-n - 1 : -1].max())
        ll = float(l[-n - 1 : -1].min())
        e20 = float(ema(c, 20)[-1])
        e50 = float(ema(c, 50)[-1])
        if math.isnan(e20) or math.isnan(e50):
            return None
        mom7 = close / float(c[-43]) - 1.0 if len(c) >= 43 else 0.0
        if close > hh and e20 > e50 and close > e50 and mom7 > 0:
            side = "LONG"
        elif close < ll and e20 < e50 and close < e50 and mom7 < 0:
            side = "SHORT"
        else:
            return None
        if ctx.regime.name == "CHAOS":
            return None
        sgn = 1 if side == "LONG" else -1
        at4 = float(atr(h, l, c, 14)[-1])
        if not math.isfinite(at4) or at4 <= 0:
            return None
        ext = sgn * (close - e20) / at4
        if ext > 3.5:  # trendin çok gerisinde kalmış kovalamaca
            return None
        stop = close - sgn * cfg.s4_atr_mult * at4
        dist = abs(close - stop) / close
        if dist < 0.015:
            stop = close - sgn * 0.015 * close
        elif dist > 0.15:
            return None
        adx4 = float(adx(h, l, c, 14)[-1])
        if not math.isfinite(adx4):
            adx4 = 0.0
        fr = ctx.funding_rate(symbol)
        reg = ctx.regime.name
        aligned = (reg == "BULL" and side == "LONG") or (reg == "BEAR" and side == "SHORT")
        score = (
            50 + clamp01((adx4 - 18.0) / 22.0) * 20 + clamp01(abs(mom7) / 0.25) * 12
            + (10 if aligned else 0) - clamp01(ext - 2.0) * 10 - clamp01(sgn * fr / 0.0005) * 10
        )
        reasons = [
            f"4 saatlik kapanış son {n} mumun {'zirvesinin üstünde' if side == 'LONG' else 'dibinin altında'}",
            f"4 saatlik EMA20 {'>' if side == 'LONG' else '<'} EMA50, trend gücü ADX {adx4:.0f}",
            f"7 günlük getiri %{mom7 * 100:+.1f} (trend yönünde)",
            f"Piyasa rejimi: {ctx.regime.label}",
        ]
        warnings: list[str] = []
        if ext > 2.5:
            warnings.append("Fiyat 4 saatlik ortalamadan uzaklaşmış")
        if not aligned:
            warnings.append("İşlem genel piyasa rejimiyle aynı yönde değil")
        # 1 saatlik ATR ile iz süren stop: ~ k×ATR(4s) mesafesi korunur
        h1, l1, c1 = a["h"][-100:], a["l"][-100:], a["c"][-100:]
        at1 = float(atr(h1, l1, c1, 14)[-1])
        trail = max(3.0, cfg.s4_atr_mult * at4 / at1) if math.isfinite(at1) and at1 > 0 else 5.0
        return make_signal(
            symbol=symbol, side=side, strategy=self.code, strategy_name=self.name, timeframe=self.timeframe,
            bar_time=int(a["t"][-1]), now=ctx.now, ttl_ms=max(ctx.scanner.signal_ttl_min, 120) * 60_000,
            entry=close, stop=stop, tp_spec=[(2.0, 0.3)], atr_value=at4, score=score, reasons=reasons,
            warnings=warnings,
            features={"adx_4h": adx4, "mom_7d": mom7, "ext_atr": ext, "funding": fr, "atr_pct": at4 / close * 100.0},
            trailing_atr_mult=round(min(trail, 10.0), 2), time_stop_bars=96,
        )
