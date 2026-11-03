"""Motor: TP sonrası başa-baş, iz süren stop ve zaman stopu (simülasyon broker'ı ile)."""
import pytest

from app.engine.engine import Engine
from app.events import EventBus, EventLog
from app.market.base import CandleBuffer
from app.models import Candle
from app.notify.telegram import TelegramNotifier
from app.risk.manager import RiskManager
from app.util import now_ms


def _buffer(base: float, n: int = 60, start: int | None = None) -> CandleBuffer:
    buf = CandleBuffer("15m")
    t0 = (start if start is not None else now_ms() - n * 900_000)
    t0 -= t0 % 900_000
    buf.load([Candle(t=t0 + i * 900_000, o=base, h=base + 0.5, l=base - 0.5, c=base, qv=1000, tbq=500) for i in range(n)])
    return buf


@pytest.fixture
async def engine(db, store, market):
    await store.update("paper", {"slippage_bps": 0.0, "starting_balance": 1000.0})
    for sym in ("SOLUSDT", "ETHUSDT"):
        market.buffers[(sym, "15m")] = _buffer(100.0)
    bus = EventBus()
    eng = Engine(
        db=db, store=store, bus=bus, events=EventLog(db, bus), risk=RiskManager(store, db),
        notifier=TelegramNotifier(store), market_factory=lambda env: market, synthetic=False,
    )
    await eng.start()
    yield eng
    await eng.stop()


async def test_tp_breakeven_then_trailing(engine, market):
    pos = await engine.manual_order("SOLUSDT", "LONG", 98.0, None, 0.01, None)
    assert pos.entry_price == 100.0 and pos.take_profits[0].price == 103.0 and pos.trailing_atr_mult == 3.0

    market.set("SOLUSDT", 103.2)
    await engine.broker.on_price("SOLUSDT", 103.2, 103.2, now_ms())
    assert pos.take_profits[0].filled
    assert pos.breakeven_done and pos.stop_price == pytest.approx(100.1)
    assert pos.trailing_active and engine.broker.pos["SOLUSDT"].stop == pytest.approx(100.1)

    buf = market.buffers[("SOLUSDT", "15m")]
    t = buf.closed[-1].t
    for i, px in enumerate((104, 106, 108, 110), start=1):
        buf.update(Candle(t=t + i * 900_000, o=px - 1, h=px + 0.5, l=px - 1.5, c=px, qv=1000, tbq=600, closed=True))
    market.set("SOLUSDT", 109.5)
    pos.refresh_runtime(109.5)
    old = pos.stop_price
    await engine._manage_on_bar(pos)
    assert pos.stop_price > old + 1.0
    assert pos.stop_price < 109.5
    assert any(e.kind == "trailing" for e in pos.events)


async def test_time_stop_closes_stalled_trade(engine, market):
    pos = await engine.manual_order("ETHUSDT", "SHORT", 102.0, None, 0.005, None)
    pos.time_stop_at = now_ms() - 1
    await engine._manage_on_bar(pos)
    assert pos.status == "CLOSED" and "Zaman stopu" in (pos.close_reason or "")
    assert pos.id not in engine.positions


async def test_breakeven_refused_when_price_not_past_it(engine, market):
    pos = await engine.manual_order("ETHUSDT", "LONG", 98.0, None, 0.005, None)
    from app.engine.engine import EngineError

    with pytest.raises(EngineError, match="başa-baş"):
        await engine.breakeven(pos.id)
