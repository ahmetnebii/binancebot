from decimal import Decimal

import pytest

from app.exchange.broker_base import AccountInfo, BrokerError, OpenRequest, TPOrder
from app.exchange.paper import PaperBroker
from app.models import Position, SymbolInfo
from app.risk.manager import RiskManager, loss_streak
from app.util import now_ms


async def _paper(db, store, market, balance=1000.0):
    await store.update("paper", {"starting_balance": balance, "slippage_bps": 0.0})
    b = PaperBroker(db, market, lambda: store.paper)
    events = []

    async def on_reduce(ev):
        events.append(ev)

    funding = []

    async def on_funding(sym, amount, ts):
        funding.append(amount)

    b.on_reduce = on_reduce
    b.on_funding = on_funding
    await b.start()
    return b, events, funding


def _req(symbol="BTCUSDT", side="LONG", qty="1", lev=5, stop="98", tps=(), mode="market", ref=100.0, mmr=0.005):
    return OpenRequest(position_id="pos_x", symbol=symbol, side=side, qty=Decimal(qty), leverage=lev, ref_price=ref,
                       stop_price=Decimal(stop), tps=list(tps), entry_mode=mode, max_slippage_pct=0.002, mmr=mmr)


async def test_paper_tp_then_stop(db, store, market):
    b, events, _ = await _paper(db, store, market)
    fill = await b.open(_req(tps=[TPOrder(1, Decimal("103"), Decimal("0.4"))]))
    assert fill.price == 100.0 and abs(fill.fee - 0.05) < 1e-12
    market.set("BTCUSDT", 103.5)
    await b.on_price("BTCUSDT", 103.5, 103.5, now_ms())
    assert events[-1].kind == "tp" and events[-1].tp_index == 1
    assert abs(events[-1].realized_pnl - 1.2) < 1e-9
    market.set("BTCUSDT", 97.9)
    await b.on_price("BTCUSDT", 97.9, 97.9, now_ms())
    assert events[-1].kind == "sl" and abs(events[-1].qty - 0.6) < 1e-12
    assert "BTCUSDT" not in b.pos
    acct = await b.account()
    expected = 1000 - 0.05 + 1.2 - 103 * 0.4 * 0.0002 + (-0.6 * 2.1) - 97.9 * 0.6 * 0.0005
    assert abs(acct.wallet - expected) < 1e-9


async def test_paper_short_liquidation(db, store, market):
    b, events, _ = await _paper(db, store, market)
    await b.open(_req(side="SHORT", lev=10, stop="150"))
    liq = b.pos["BTCUSDT"].liq
    assert abs(liq - 100 * (1 + 0.1 - 0.005)) < 1e-9
    market.set("BTCUSDT", 111.0)
    await b.on_price("BTCUSDT", 111.0, 111.0, now_ms())
    assert events[-1].kind == "liquidation"


async def test_paper_funding_applies_once(db, store, market):
    b, _, funding = await _paper(db, store, market)
    await b.open(_req())
    t1 = now_ms() + 1000
    market.set_funding("BTCUSDT", 0.0001, t1)
    await b.on_price("BTCUSDT", 100.0, 100.0, now_ms())
    market.set_funding("BTCUSDT", 0.0001, t1 + 8 * 3600_000)
    await b.on_price("BTCUSDT", 100.0, 100.0, now_ms())
    await b.on_price("BTCUSDT", 100.0, 100.0, now_ms())
    assert len(funding) == 1 and abs(funding[0] - (-0.01)) < 1e-12  # long pozitif funding öder


async def test_paper_guards(db, store, market):
    b, _, _ = await _paper(db, store, market, balance=50)
    with pytest.raises(BrokerError):
        await b.open(_req(qty="10", lev=2))  # marjin yetersiz
    market.set("BTCUSDT", 101.0)
    with pytest.raises(BrokerError):
        await b.open(_req(qty="0.1", mode="ioc", ref=100.0))  # kayma sınırı


def _plan(rm, store, market, **kw):
    args = dict(
        env="paper", symbol="SOLUSDT", side="LONG", entry=100.0, stop=98.0, score=75.0, source="manual",
        info=market.symbols["SOLUSDT"], account=AccountInfo(1000, 1000, 0, 1000, 0), open_positions=[],
        marks=market.mark, regime_mult=1.0, taker_fee=0.0005, slippage=0.0005, mmr=0.005, bracket_cap=50,
        cls="large", corr=lambda a, b: 0.0, streak=0,
    )
    args.update(kw)
    return rm.plan(**args)


def _open_pos(symbol, side="LONG", qty=1.0, entry=100.0, stop=98.0):
    return Position(id=f"p_{symbol}", env="paper", symbol=symbol, side=side, strategy="S1", strategy_name="x",
                    opened_at=0, entry_price=entry, qty=qty, initial_qty=qty, leverage=5, margin=entry * qty / 5,
                    notional=entry * qty, stop_price=stop, initial_stop=stop, risk_usdt=abs(entry - stop) * qty)


async def test_risk_plan_ok_and_rejections(db, store, market):
    rm = RiskManager(store, db)
    p = _plan(rm, store, market)
    assert p.ok and p.leverage >= 1 and p.risk_pct <= store.risk.base_risk_pct + 1e-9
    assert any("Kaldıraç" in n for n in p.notes)
    assert not _plan(rm, store, market, stop=101.0).ok
    full = [_open_pos(f"X{i}USDT") for i in range(5)]
    r = _plan(rm, store, market, open_positions=full)
    assert not r.ok and "limiti" in r.reason
    tiny = _plan(rm, store, market, account=AccountInfo(5, 5, 0, 5, 0))
    assert not tiny.ok and "minimum" in tiny.reason


async def test_risk_heat_scales_down(db, store, market):
    rm = RiskManager(store, db)
    # 3 açık pozisyon toplam 36 USDT risk; ısı limiti 40 → yeni işlem en fazla ~4 USDT risk alabilir
    opens = [_open_pos(s, qty=6.0) for s in ("AUSDT", "BUSDT", "CUSDT")]
    for s in ("AUSDT", "BUSDT", "CUSDT"):
        market.set(s, 100.0)
    r = _plan(rm, store, market, open_positions=opens)
    assert r.ok and r.risk_usdt <= 4.0 + 1e-6
    assert any("ısı" in n or "risk limiti" in n for n in r.notes)


async def test_capital_tiers_and_breakers(db, store):
    rm = RiskManager(store, db)
    assert rm.effective_max_positions(150) == 2
    assert rm.effective_max_positions(5000) == store.risk.max_positions
    t0 = now_ms()
    await rm.update_equity("paper", 1000.0, t0)
    await rm.update_equity("paper", 960.0, t0 + 1000)
    br = {b.code: b for b in rm.breakers("paper", store.runtime, [])}
    assert br["daily_loss"].active and not br["max_drawdown"].active


def test_loss_streak_counts_recent_losses():
    def closed(net, ts):
        p = Position(id=f"c{ts}", env="paper", symbol="X", side="LONG", strategy="S1", strategy_name="x", opened_at=0,
                     entry_price=1, qty=0, initial_qty=1, leverage=1, margin=1, notional=1, stop_price=0.9,
                     initial_stop=0.9, status="CLOSED", closed_at=ts, realized_pnl=net)
        return p
    n, last = loss_streak([closed(-1, 3), closed(-2, 2), closed(5, 1)])
    assert n == 2 and last == 3


def test_symbolinfo_defaults_are_sane():
    s = SymbolInfo(symbol="ABCUSDT")
    assert s.contract_type == "PERPETUAL" and s.status == "TRADING"
