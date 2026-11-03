"""Binance broker mantığı: sahte REST ile (ağ gerekmez)."""
from decimal import Decimal

import pytest

from app.exchange.binance_broker import BinanceBroker
from app.exchange.binance_client import BinanceAPIError
from app.exchange.broker_base import BrokerError, OpenRequest, TPOrder


class FakeREST:
    def __init__(self, fail_algo: bool = False):
        self.calls: list[tuple[str, str, dict]] = []
        self.fail_algo = fail_algo
        self.session = None

    async def request(self, method, path, params=None, signed=False, key_only=False):
        p = dict(params or {})
        self.calls.append((method, path, p))
        if path == "/fapi/v1/commissionRate":
            return {"makerCommissionRate": "0.0002", "takerCommissionRate": "0.0005"}
        if path == "/fapi/v1/algoOrder" and method == "POST" and self.fail_algo:
            raise BinanceAPIError(400, -2021, "Order would immediately trigger.")
        if path == "/fapi/v1/order" and method == "POST":
            if p.get("newClientOrderId", "").endswith("-E"):
                return {"executedQty": p["quantity"], "avgPrice": "100.05"}
            if p.get("type") == "MARKET":
                return {"executedQty": p["quantity"], "avgPrice": "99.9"}
            return {"orderId": 1}
        return {}

    def find(self, method, path):
        return [c for c in self.calls if c[0] == method and c[1] == path]


def _broker(market, rest):
    b = BinanceBroker("testnet", rest, "wss://x", "/private/ws?listenKey={listenKey}", market)
    events = []

    async def on_reduce(ev):
        events.append(ev)

    b.on_reduce = on_reduce
    return b, events


def _req():
    return OpenRequest(
        position_id="pos_abcdef123456", symbol="BTCUSDT", side="LONG", qty=Decimal("1.000"), leverage=4,
        ref_price=100.0, stop_price=Decimal("98.0"), tps=[TPOrder(1, Decimal("103.0"), Decimal("0.400"))],
        entry_mode="ioc", max_slippage_pct=0.002, mmr=0.005,
    )


async def test_open_places_entry_algo_stop_and_reduce_only_tp(market):
    rest = FakeREST()
    b, _ = _broker(market, rest)
    fill = await b.open(_req())
    assert fill.price == 100.05 and fill.qty == 1.0
    assert rest.find("POST", "/fapi/v1/marginType")[0][2]["marginType"] == "ISOLATED"
    assert rest.find("POST", "/fapi/v1/leverage")[0][2]["leverage"] == 4
    entry = rest.find("POST", "/fapi/v1/order")[0][2]
    assert entry["type"] == "LIMIT" and entry["timeInForce"] == "IOC" and entry["price"] == "100.2"
    algo = rest.find("POST", "/fapi/v1/algoOrder")[0][2]
    assert algo["algoType"] == "CONDITIONAL" and algo["type"] == "STOP_MARKET"
    assert algo["triggerPrice"] == "98" and algo["closePosition"] is True and algo["workingType"] == "MARK_PRICE"
    assert algo["side"] == "SELL" and algo["clientAlgoId"].startswith("bb-")
    tp = rest.find("POST", "/fapi/v1/order")[1][2]
    assert tp["reduceOnly"] is True and tp["type"] == "LIMIT" and tp["timeInForce"] == "GTC" and tp["quantity"] == "0.4"


async def test_stop_failure_closes_position_for_safety(market):
    rest = FakeREST(fail_algo=True)
    b, _ = _broker(market, rest)
    with pytest.raises(BrokerError):
        await b.open(_req())
    closes = [c for c in rest.find("POST", "/fapi/v1/order") if c[2].get("type") == "MARKET"]
    assert closes and closes[0][2]["reduceOnly"] is True and closes[0][2]["side"] == "SELL"
    assert "BTCUSDT" not in b._sides  # kayıt geri alındı


async def test_replace_stop_places_new_before_cancelling_old(market):
    rest = FakeREST()
    b, _ = _broker(market, rest)
    await b.open(_req())
    first_id = b._sl_ids["BTCUSDT"]
    await b.replace_stop("BTCUSDT", "LONG", Decimal("100.1"))
    order = [(m, p) for m, p, _ in rest.calls if p == "/fapi/v1/algoOrder"]
    assert order[-2:] == [("POST", "/fapi/v1/algoOrder"), ("DELETE", "/fapi/v1/algoOrder")]
    assert rest.calls[-1][2]["clientAlgoId"] == first_id


async def test_user_stream_event_classification(market):
    rest = FakeREST()
    b, events = _broker(market, rest)
    await b.open(_req())
    prefix = b._cid("BTCUSDT", "")

    def otu(cid, side="SELL", qty="0.4", price="103"):
        return {"e": "ORDER_TRADE_UPDATE", "E": 1, "o": {"s": "BTCUSDT", "c": cid, "S": side, "x": "TRADE",
                                                          "l": qty, "L": price, "n": "0.01", "N": "USDT", "rp": "1.2"}}

    await b._on_user_event(otu(prefix + "T1-1"))
    assert events[-1].kind == "tp" and events[-1].tp_index == 1 and events[-1].realized_pnl == 1.2
    await b._on_user_event(otu(prefix + "X1"))  # motorun kendi kapatma emri: yok sayılır
    assert len(events) == 1
    await b._on_user_event(otu("autoclose-123"))
    assert events[-1].kind == "liquidation"
    await b._on_user_event(otu("someAlgoTriggeredId", qty="0.6", price="98"))
    assert events[-1].kind == "sl"
    await b._on_user_event(otu("entry-fill", side="BUY"))  # pozisyonu artıran işlem: yok sayılır
    assert len(events) == 3
