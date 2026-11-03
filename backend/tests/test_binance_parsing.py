import asyncio
import base64
import json
from urllib.parse import parse_qs

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.exchange.binance_client import BinanceREST
from app.market.binance import BinanceMarketData, parse_exchange_info, parse_klines
from app.market.universe import select_universe
from app.market.base import Ticker
from app.settings import ScannerSettings
from app.util import now_ms


def test_hmac_signature_matches_binance_docs_example():
    # Binance API dokümantasyonundaki örnek anahtar ve sorgu
    rest = BinanceREST("https://example", api_key="x", api_secret="NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j")
    q = "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"
    assert rest.sign(q) == "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


def test_ed25519_signature_is_valid_and_url_encoded():
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    rest = BinanceREST("https://example", api_key="x", private_key_pem=pem)
    qs = rest.build_query({"symbol": "BTCUSDT", "closePosition": True}, signed=True)
    payload, sig_part = qs.rsplit("&signature=", 1)
    assert "closePosition=true" in payload
    assert all(ch not in sig_part for ch in "+/=")  # base64 karakterleri URL kodlanmış
    sig = base64.b64decode(parse_qs("s=" + sig_part)["s"][0])
    key.public_key().verify(sig, payload.encode())  # geçersizse hata fırlatır


def test_parse_exchange_info_and_klines():
    data = {"symbols": [{
        "symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING",
        "onboardDate": 1569398400000, "deliveryDate": 4133404800000,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "maxQty": "120"},
            {"filterType": "MIN_NOTIONAL", "notional": "100"},
        ],
    }]}
    info = parse_exchange_info(data)["BTCUSDT"]
    assert info.tick_size == "0.10" and info.market_max_qty == "120" and info.min_notional == "100"
    now = now_ms()
    rows = [[0, "1", "2", "0.5", "1.5", "10", 59_999, "15", 5, "6", "9", "0"],
            [60_000, "1.5", "2", "1", "1.8", "10", now + 60_000, "18", 5, "6", "11", "0"]]
    ks = parse_klines(rows, now)
    assert ks[0].closed and not ks[1].closed and ks[1].tbq == 11.0 and ks[0].qv == 15.0


async def test_ws_message_routing_updates_buffers_and_emits_bar_close():
    md = BinanceMarketData("https://x", "wss://x", "/market/stream", lambda: ScannerSettings(), events=None)  # type: ignore[arg-type]
    closed = []

    async def on_close(sym, tf, c):
        closed.append((sym, tf, c.c))

    md.bar_close_handlers.append(on_close)
    k = {"t": 0, "T": 899_999, "s": "BTCUSDT", "i": "15m", "o": "1", "c": "2", "h": "3", "l": "0.5", "v": "10", "q": "20", "Q": "12", "x": True}
    md._on_message(json.dumps({"stream": "btcusdt@kline_15m", "data": {"e": "kline", "E": 1, "s": "BTCUSDT", "k": k}}))
    md._on_message(json.dumps({"stream": "!markPrice@arr@1s", "data": [{"e": "markPriceUpdate", "E": 5, "s": "BTCUSDT", "p": "2.01", "i": "2.0", "r": "0.0001", "T": 123}]}))
    md._on_message(json.dumps({"stream": "!miniTicker@arr", "data": [{"e": "24hrMiniTicker", "E": 6, "s": "BTCUSDT", "c": "2.02", "o": "1.9", "h": "2.1", "l": "1.8", "v": "100", "q": "5000"}]}))
    md._on_message(json.dumps({"result": None, "id": 1}))
    await asyncio.sleep(0)
    assert closed == [("BTCUSDT", "15m", 2.0)]
    assert md.buffer("BTCUSDT", "15m").last_closed().tbq == 12.0
    assert md.mark("BTCUSDT") == 2.01 and md.funding("BTCUSDT").funding_rate == 0.0001
    assert md.price("BTCUSDT") == 2.02 and md.tickers["BTCUSDT"].quote_volume == 5000.0


def test_universe_excludes_delist_young_and_illiquid():
    from app.models import SymbolInfo
    now = now_ms()
    syms = {
        "BTCUSDT": SymbolInfo(symbol="BTCUSDT", tick_size="0.1", onboard_date=now - 400 * 86_400_000, delivery_date=4133404800000),
        "DELUSDT": SymbolInfo(symbol="DELUSDT", tick_size="0.0001", onboard_date=now - 400 * 86_400_000, delivery_date=now + 3 * 86_400_000),
        "NEWUSDT": SymbolInfo(symbol="NEWUSDT", tick_size="0.0001", onboard_date=now - 2 * 86_400_000, delivery_date=4133404800000),
        "LOWUSDT": SymbolInfo(symbol="LOWUSDT", tick_size="0.0001", onboard_date=now - 400 * 86_400_000, delivery_date=4133404800000),
    }
    t = lambda qv: Ticker(last=10.0, open=10.0, high=10.0, low=10.0, quote_volume=qv, change_pct=0.0, ts=now)  # noqa: E731
    tickers = {"BTCUSDT": Ticker(last=60000, open=1, high=1, low=1, quote_volume=1e10, change_pct=0, ts=now),
               "DELUSDT": t(1e9), "NEWUSDT": t(1e9), "LOWUSDT": t(1e5)}
    assert select_universe(syms, tickers, ScannerSettings(), now) == ["BTCUSDT"]
