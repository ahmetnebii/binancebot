"""S5 grafik formasyonu tanıma: nesnel kurallar, ayna simetrisi, gelecek bilgisi yok."""
import asyncio

import numpy as np

from app.backtest.data import SyntheticHistory
from app.backtest.simulator import Backtester, BacktestConfig
from app.features.indicators import atr
from app.market.synthetic import SPECS
from app.settings import RiskSettings, ScannerSettings, StrategySettings
from app.strategies.patterns import detect_bullish, pivots
from app.util import now_ms


def _series(points):
    t = np.arange(points[-1][0] + 1)
    c = np.interp(t, [p[0] for p in points], [p[1] for p in points])
    return c + 0.2, c - 0.2, c


def _detect(points):
    h, l, c = _series(points)  # noqa: E741
    at = float(atr(h, l, c, 14)[-1])
    return detect_bullish(h, l, c, at, 5, 1.0)


IHS = [(0, 110), (20, 100), (35, 106), (55, 94), (75, 106), (95, 100), (115, 105.9), (116, 107)]
DBL = [(0, 110), (20, 100), (40, 106), (60, 100.3), (80, 105.9), (81, 107)]
TRI = [(0, 100), (10, 106), (25, 101), (40, 106), (55, 103), (70, 105.8), (71, 107)]


def test_pivots_need_k_bars_on_right():
    x = np.array([1, 2, 3, 9, 3, 2, 1, 0, 0, 0], dtype=float)
    assert list(pivots(x, 3, True)) == [3]
    assert list(pivots(x[:6], 3, True)) == []  # sağda yalnızca 2 mum: henüz kesinleşmedi


def test_detects_classic_patterns():
    f = _detect(IHS)
    assert f is not None and f.kind == "hs" and abs(f.level - 106.2) < 1e-6 and f.height > 10
    f = _detect(DBL)
    assert f is not None and f.kind == "double" and f.stop < 100
    f = _detect(TRI)
    assert f is not None and f.kind == "triangle" and abs(f.level - 106.2) < 1e-6


def test_no_signal_before_breakout_or_after_stale_breakout():
    pre = IHS[:-1] + [(116, 105.95)]  # henüz kırılmadı
    assert _detect(pre) is None
    late = IHS[:-1] + [(116, 107), (117, 107.5)]  # kırılım bir önceki mumdaydı: taze değil
    assert _detect(late) is None


def test_bearish_mirror():
    # boğa formasyonunun aynası ayı formasyonudur: strateji ayıları ters çevrilmiş seride arar
    h, l, c = _series(IHS)  # noqa: E741
    top_h, top_l, top_c = 200 - l, 200 - h, 200 - c  # omuz-baş-omuz tepe
    at = float(atr(top_h, top_l, top_c, 14)[-1])
    assert detect_bullish(top_h, top_l, top_c, at, 5, 1.0) is None  # boğa olarak görülmez
    f = detect_bullish(-top_l, -top_h, -top_c, at, 5, 1.0)
    assert f is not None and f.kind == "hs"


def test_s5_runs_in_backtest():
    syms = [s[0] for s in SPECS][:12]
    end = now_ms()
    end -= end % 900_000
    start = end - 45 * 86_400_000
    data = asyncio.run(SyntheticHistory().load(syms, start - 12 * 86_400_000, end, lambda f, m: None, lambda: False))
    st = StrategySettings(s1_enabled=False, s2_enabled=False, s5_enabled=True, s5_vol_z=0.0)
    cfg = BacktestConfig(symbols=syms, start_ms=start, end_ms=end, initial_equity=1000.0, s1=False, s2=False, s3=False, s5=True,
                         slippage_bps=3.0, taker_fee=0.0005, maker_fee=0.0002, risk=RiskSettings(),
                         strategies=st, scanner=ScannerSettings())
    raw = Backtester(cfg, data, {}, lambda f, m: None, lambda: False).run()
    assert {t["strategy"] for t in raw["trades"]} <= {"S5"}
    assert raw["trades"], "sentetik veride en az bir formasyon işlemi beklenir"
