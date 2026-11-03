"""S3 — Ortalamaya Dönüş (yalnızca yatay piyasa rejiminde).

Fiyat, zayıf trendli bir piyasada Bollinger bandının dışına taşıp RSI aşırı bölgeye
girdiğinde ve kayan VWAP'tan istatistiksel olarak uzaklaştığında ortalamaya dönüş
işlemi önerir. Hedef Bollinger orta bandıdır; ilerlemezse kısa zaman stopuyla çıkılır.

Varsayılan olarak KAPALIDIR: önce Backtest ile doğrulanmalıdır.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ..features.indicators import adx, atr, bollinger, log_volume_z, rsi
from ..models import Signal
from .base import StrategyContext, clamp01, make_signal


class MeanReversion:
    code = "S3"
    name = "Ortalamaya Dönüş"
    timeframe = "15m"

    def evaluate(self, symbol: str, ctx: StrategyContext) -> Optional[Signal]:
        if ctx.regime.name != "RANGE":
            return None
        cfg = ctx.settings
        a = ctx.arrays(symbol, "15m", 150)
        b = ctx.arrays(symbol, "1h", 60)
        if a is None or b is None:
            return None
        c, h, l, v, qv = a["c"][-300:], a["h"][-300:], a["l"][-300:], a["v"][-300:], a["qv"][-300:]  # noqa: E741
        close = float(c[-1])
        mid, up, lo, _ = bollinger(c, 20, cfg.s3_bb_k)
        if math.isnan(mid[-1]):
            return None
        if close < lo[-1]:
            side = "LONG"
        elif close > up[-1]:
            side = "SHORT"
        else:
            return None
        sgn = 1 if side == "LONG" else -1
        r = float(rsi(c, 14)[-1])
        if not math.isfinite(r):
            return None
        if (side == "LONG" and r >= cfg.s3_rsi) or (side == "SHORT" and r <= 100.0 - cfg.s3_rsi):
            return None
        adx1 = float(adx(b["h"][-300:], b["l"][-300:], b["c"][-300:], 14)[-1])
        if not math.isfinite(adx1) or adx1 >= 25.0:
            return None
        vol_sum = float(v[-96:].sum())
        if vol_sum <= 0:
            return None
        vwap = float(qv[-96:].sum()) / vol_sum
        sd = float(np.std(c[-96:]))
        if sd <= 0:
            return None
        dev = (close - vwap) / sd
        if sgn * dev > -1.0:  # VWAP'tan en az 1σ uzak; daha fazlası puanı artırır
            return None
        at = float(atr(h, l, c, 14)[-1])
        if not math.isfinite(at) or at <= 0:
            return None
        stop = close - sgn * 1.5 * at
        dist = abs(close - stop) / close
        if dist < 0.006:
            stop = close - sgn * 0.006 * close
        elif dist > 0.04:
            return None
        target = float(mid[-1])
        r_mult = sgn * (target - close) / abs(close - stop)
        if r_mult < 0.8:
            return None
        vol_z = log_volume_z(qv, 96)
        rsi_depth = (cfg.s3_rsi - r) if side == "LONG" else (r - (100.0 - cfg.s3_rsi))
        score = (
            50
            + clamp01((abs(dev) - 1.0) / 2.0) * 15
            + clamp01(rsi_depth / 10.0) * 10
            + clamp01((25.0 - adx1) / 10.0) * 10
            + (5 if vol_z < 2.0 else -5)
        )
        band = "alt" if side == "LONG" else "üst"
        reasons = [
            f"15 dk kapanış Bollinger {band} bandının dışında (k={cfg.s3_bb_k:g})",
            f"RSI {r:.0f}: aşırı {'satım' if side == 'LONG' else 'alım'}",
            f"Fiyat kayan VWAP'tan {abs(dev):.1f}σ uzakta",
            f"1 saatlik ADX {adx1:.0f}: trend zayıf, piyasa yatay",
            f"Hedef: Bollinger orta bandı ({r_mult:.1f}R)",
        ]
        warnings = []
        if vol_z >= 2.0:
            warnings.append("Hacim çok yüksek: hareket bir kırılımın başlangıcı olabilir")
        return make_signal(
            symbol=symbol, side=side, strategy=self.code, strategy_name=self.name, timeframe=self.timeframe,
            bar_time=int(a["t"][-1]), now=ctx.now, ttl_ms=min(ctx.scanner.signal_ttl_min, 30) * 60_000,
            entry=close, stop=stop, tp_spec=[(round(r_mult, 2), 1.0)], atr_value=at, score=score,
            reasons=reasons, warnings=warnings,
            features={"rsi": r, "vwap_dev": dev, "adx_1h": adx1, "vol_z": vol_z, "atr_pct": at / close * 100.0},
            trailing_atr_mult=None, time_stop_bars=12,
        )
