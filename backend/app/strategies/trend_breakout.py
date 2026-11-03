"""S1 — Trend Kırılımı (Donchian + hacim + üst zaman dilimi trend filtresi)."""
from __future__ import annotations

import math
from typing import Optional

from ..features.mtf import four_hour_trend
from ..features.indicators import adx, atr, bollinger, ema, log_volume_z, pct_rank_last
from ..models import Signal
from .base import StrategyContext, clamp01, make_signal


class TrendBreakout:
    code = "S1"
    name = "Trend Kırılımı"
    timeframe = "15m"

    def evaluate(self, symbol: str, ctx: StrategyContext) -> Optional[Signal]:
        cfg = ctx.settings
        a = ctx.arrays(symbol, "15m", 150)
        b = ctx.arrays(symbol, "1h", 210)
        if a is None or b is None:
            return None
        n = cfg.s1_donchian
        c = a["c"][-400:]
        h = a["h"][-400:]
        l = a["l"][-400:]  # noqa: E741
        qv = a["qv"][-400:]
        tbq = a["tbq"][-400:]
        if len(c) < max(n + 2, 120):
            return None
        close = float(c[-1])
        hh = float(h[-n - 1 : -1].max())
        ll = float(l[-n - 1 : -1].min())
        long_trig = close > hh
        short_trig = close < ll
        if not (long_trig or short_trig):
            return None

        c1, h1, l1 = b["c"][-500:], b["h"][-500:], b["l"][-500:]
        e50 = float(ema(c1, 50)[-1])
        e200 = float(ema(c1, 200)[-1])
        if math.isnan(e50) or math.isnan(e200):
            return None
        if long_trig and c1[-1] > e200 and e50 > e200:
            side = "LONG"
        elif short_trig and c1[-1] < e200 and e50 < e200:
            side = "SHORT"
        else:
            return None
        reg = ctx.regime.name
        if reg == "CHAOS" or (side == "LONG" and reg == "BEAR") or (side == "SHORT" and reg == "BULL"):
            return None
        sgn = 1 if side == "LONG" else -1

        at = float(atr(h, l, c, 14)[-1])
        if not math.isfinite(at) or at <= 0:
            return None
        vol_z = log_volume_z(qv, 96)
        if vol_z < cfg.s1_vol_z:
            return None
        e20 = float(ema(c, 20)[-1])
        ext = sgn * (close - e20) / at
        if ext > 3.0:
            return None
        fr = ctx.funding_rate(symbol)
        if (side == "LONG" and fr > 0.0005) or (side == "SHORT" and fr < -0.0005):
            return None

        stop = close - sgn * cfg.s1_atr_mult * at
        dist = abs(close - stop) / close
        if dist < 0.008:
            stop = close - sgn * 0.008 * close
            dist = 0.008
        if dist > 0.06:
            return None

        taker = float(tbq[-1] / qv[-1]) if qv[-1] > 0 else 0.5
        taker_dir = taker if side == "LONG" else 1.0 - taker
        adx1 = float(adx(h1, l1, c1, 14)[-1])
        if not math.isfinite(adx1):
            adx1 = 0.0
        _, _, _, width = bollinger(c, 20, 2.0)
        squeeze_rank = pct_rank_last(width[:-1], 100)
        squeeze = squeeze_rank <= 0.2
        rs = 0.0
        btc = ctx.arrays("BTCUSDT", "1h", 30)
        if btc is not None and symbol != "BTCUSDT" and len(c1) >= 25:
            bc = btc["c"]
            rs = (c1[-1] / c1[-25] - 1.0) - (bc[-1] / bc[-25] - 1.0)

        # Zaman dilimi uyumu: 1 saat filtreyle zaten uyumlu; 15 dk ve 4 saat ek teyit
        e50_15 = float(ema(c, 50)[-1])
        tf15_ok = (sgn * (close - e50_15) > 0) and (sgn * (e20 - e50_15) > 0)
        t4 = four_hour_trend(ctx.market, symbol)
        want = "up" if side == "LONG" else "down"
        aligned = 1 + int(tf15_ok) + int(t4 == want)
        mtf_s = 5.0 * (aligned - 1) - (5.0 if t4 is not None and t4 not in (want, "flat") else 0.0)

        trend_s = clamp01((adx1 - 15.0) / 25.0) * 15
        vol_s = 5 + clamp01((vol_z - 2.0) / 3.0) * 15
        taker_s = clamp01((taker_dir - 0.5) / 0.2) * 10
        squeeze_s = 10 if squeeze else 0
        rs_s = clamp01(sgn * rs / 0.05) * 10
        regime_s = 5 if (reg == "BULL" and side == "LONG") or (reg == "BEAR" and side == "SHORT") else 0
        fund_pen = clamp01(sgn * fr / 0.0005) * 10
        ext_pen = clamp01(ext - 2.0) * 10
        score = 42 + trend_s + vol_s + taker_s + squeeze_s + rs_s + regime_s + mtf_s - fund_pen - ext_pen

        word_level = "zirvesinin üstünde" if side == "LONG" else "dibinin altında"
        reasons = [
            f"15 dk kapanış son {n} barın {word_level} (Donchian kırılımı)",
            f"Hacim z-skoru {vol_z:.1f}: hacim ortalamanın belirgin üstünde",
            f"1 saatlik trend uyumlu: EMA50 {'>' if side == 'LONG' else '<'} EMA200 (ADX {adx1:.0f})",
            f"Agresif {'alım' if side == 'LONG' else 'satım'} oranı %{taker_dir * 100:.0f}",
        ]
        reasons.append(f"Zaman dilimi uyumu: {aligned}/3 (15 dk · 1 sa · 4 sa)")
        if squeeze:
            reasons.append("Kırılım öncesi volatilite sıkışması")
        if rs_s > 0:
            reasons.append(f"BTC'ye göre 24 saatlik göreli güç %{rs * 100:+.1f}")
        warnings: list[str] = []
        if ext > 2.2:
            warnings.append("Fiyat kısa vadeli ortalamadan uzaklaşmış (kovalama riski)")
        if fund_pen > 5:
            warnings.append("Funding yüksek: pozisyonlar kalabalık olabilir")
        if t4 is not None and t4 not in (want, "flat"):
            warnings.append("4 saatlik trend ters yönde: kırılım daha büyük trende karşı")

        ttl = ctx.scanner.signal_ttl_min * 60_000
        return make_signal(
            symbol=symbol, side=side, strategy=self.code, strategy_name=self.name, timeframe=self.timeframe,
            bar_time=int(a["t"][-1]), now=ctx.now, ttl_ms=ttl, entry=close, stop=stop,
            tp_spec=[(1.5, 0.4), (3.0, 0.3)], atr_value=at, score=score, reasons=reasons, warnings=warnings,
            features={
                "vol_z": vol_z, "adx_1h": adx1, "ext_atr": ext, "taker_ratio": taker_dir, "funding": fr,
                "squeeze_rank": squeeze_rank, "rs_24h": rs, "atr_pct": at / close * 100.0,
                "mtf_aligned": float(aligned),
            },
            trailing_atr_mult=3.0, time_stop_bars=24,
        )
