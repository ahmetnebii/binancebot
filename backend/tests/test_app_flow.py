"""Uçtan uca: sentetik geliştirme verisi + simülasyon broker'ı ile API ve motor akışı."""
import time

import pytest
from fastapi.testclient import TestClient

H = {"X-BB-Client": "ui"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BB_DATA_SOURCE", "synthetic")
    monkeypatch.setenv("BB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BB_FRONTEND_DIST", str(tmp_path / "nofrontend"))
    from app.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _wait_signals(c, timeout=15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        sigs = c.get("/api/signals").json()
        if sigs:
            return sigs
        time.sleep(0.5)
    raise AssertionError("fırsat üretilmedi")


def test_auth_csrf_and_secret_masking(client):
    assert client.get("/api/auth/state").json() == {"setup_required": True, "authenticated": False}
    assert client.get("/api/status").status_code == 401
    assert client.post("/api/auth/setup", json={"password": "kisa"}, headers=H).status_code == 400
    assert client.post("/api/auth/setup", json={"password": "uzun-ve-guclu-1"}).status_code == 403
    assert client.post("/api/auth/setup", json={"password": "uzun-ve-guclu-1"}, headers=H).status_code == 200
    assert client.get("/api/status").json()["data_source"] == "synthetic"

    secret = "S3cr3tValueThatMustNeverLeak9876"
    r = client.put("/api/settings/connection", json={"api_key": "APIKEY-abcd1234", "api_secret": secret}, headers=H)
    assert r.status_code == 200
    view = r.json()["connection"]
    assert view["api_key_set"] and view["api_secret_set"] and view["api_key_hint"] == "••••1234"
    assert secret not in r.text and "APIKEY-abcd1234" not in client.get("/api/settings").text
    assert client.put("/api/settings/connection", json={"env": "live"}, headers=H).status_code == 400

    tok = "123456:ABCDEFghijklmnopQRSTUV"
    r = client.put("/api/settings/telegram", json={"bot_token": tok, "chat_id": "42", "enabled": True}, headers=H)
    assert r.status_code == 200 and tok not in r.text and r.json()["telegram"]["bot_token_set"]
    assert client.put("/api/settings/telegram", json={"chat_id": "abc"}, headers=H).status_code == 422

    r = client.put("/api/settings/risk", json={"profile": "temkinli"}, headers=H)
    assert r.json()["risk"]["base_risk_pct"] == 0.005 and r.json()["risk"]["max_positions"] == 3
    r = client.put("/api/settings/risk", json={"base_risk_pct": 0.01}, headers=H)
    assert r.json()["risk"]["profile"] == "ozel"

    assert client.post("/api/settings/password", json={"current": "yanlis", "new": "yeni-parola-123"}, headers=H).status_code == 400
    assert client.post("/api/settings/password", json={"current": "uzun-ve-guclu-1", "new": "yeni-parola-123"}, headers=H).status_code == 200
    client.post("/api/auth/logout", headers=H)
    assert client.get("/api/status").status_code == 401
    assert client.post("/api/auth/login", json={"password": "uzun-ve-guclu-1"}, headers=H).status_code == 401
    assert client.post("/api/auth/login", json={"password": "yeni-parola-123"}, headers=H).status_code == 200


def test_signal_to_position_lifecycle_and_ws(client):
    client.post("/api/auth/setup", json={"password": "uzun-ve-guclu-1"}, headers=H)
    sigs = _wait_signals(client)
    sig = next(s for s in sigs if s["plan"] and s["plan"]["ok"])
    prev = client.post(f"/api/signals/{sig['id']}/preview", json={}, headers=H).json()
    assert prev["plan"]["ok"] and prev["entry"] > 0

    r = client.post(f"/api/signals/{sig['id']}/execute", json={}, headers=H)
    assert r.status_code == 200, r.text
    pos = r.json()
    assert pos["status"] == "OPEN" and pos["leverage"] >= 1 and pos["stop_price"] > 0
    assert client.post(f"/api/signals/{sig['id']}/execute", json={}, headers=H).status_code == 400  # ikinci kez işlenemez

    detail = client.get(f"/api/positions/{pos['id']}").json()
    proj = detail["projection"]
    assert proj and len(proj["cone"]) > 10 and 0 <= proj["p_target_first"] <= 1

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "chart.sub", "symbol": pos["symbol"], "interval": "1m"})
        seen = set()
        for _ in range(40):
            seen.add(ws.receive_json()["type"])
            if {"status", "positions", "kline"} <= seen:
                break
        assert {"status", "positions", "kline"} <= seen

    r = client.post(f"/api/positions/{pos['id']}/close", json={"fraction": 1.0}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "CLOSED" and r.json()["r_final"] is not None
    stats = client.get("/api/history/stats").json()
    assert stats["trades"] == 1


def test_manual_order_validation(client):
    client.post("/api/auth/setup", json={"password": "uzun-ve-guclu-1"}, headers=H)
    _wait_signals(client)
    price = next(s["price"] for s in client.get("/api/market/symbols").json() if s["symbol"] == "ETHUSDT")
    bad = client.post("/api/orders/preview", json={"symbol": "ETHUSDT", "side": "LONG", "stop_price": price * 1.02}, headers=H)
    assert bad.status_code == 200 and not bad.json()["plan"]["ok"]
    r = client.post("/api/orders/manual", json={"symbol": "ETHUSDT", "side": "SHORT", "stop_price": price * 1.02, "risk_pct": 0.005}, headers=H)
    assert r.status_code == 200, r.text
    pos = r.json()
    assert pos["side"] == "SHORT" and len(pos["take_profits"]) == 2
    assert client.post(f"/api/positions/{pos['id']}/stop", json={"price": price * 0.99}, headers=H).status_code == 400
    assert client.post(f"/api/positions/{pos['id']}/stop", json={"price": price * 1.015}, headers=H).status_code == 200
    assert client.post("/api/orders/manual", json={"symbol": "ETHUSDT", "side": "LONG", "stop_price": price * 0.98}, headers=H).status_code == 400
    assert client.post("/api/control/close-all", headers=H).json()["closed"] == 1
