"""Arayüz için canlı WebSocket akışı."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..security import SESSION_COOKIE, token_hash
from ..util import now_ms
from .deps import dumps

log = logging.getLogger(__name__)
router = APIRouter()
INTERVALS = {"1m", "5m", "15m", "1h", "4h"}
MAX_CHART_SUBS = 8


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    ctx = ws.app.state.ctx
    token = ws.cookies.get(SESSION_COOKIE)
    if not token or not await ctx.db.session_valid(token_hash(token), now_ms()):
        await ws.close(code=4401)
        return
    engine = ctx.engine
    if engine is None:
        await ws.close(code=1013)
        return
    await ws.accept()
    q = ctx.bus.subscribe()
    subs: set[tuple[str, str]] = set()

    async def sub(symbol: str, tf: str) -> None:
        key = (symbol, tf)
        if key in subs or len(subs) >= MAX_CHART_SUBS:
            return
        subs.add(key)
        engine.watched[key] = engine.watched.get(key, 0) + 1
        try:
            await engine.market.watch(symbol, tf)
        except Exception as exc:
            log.debug("Grafik aboneliği başarısız: %s", exc)

    async def unsub(symbol: str, tf: str) -> None:
        key = (symbol, tf)
        if key not in subs:
            return
        subs.discard(key)
        n = engine.watched.get(key, 0) - 1
        if n > 0:
            engine.watched[key] = n
        else:
            engine.watched.pop(key, None)
        try:
            await engine.market.unwatch(symbol, tf)
        except Exception:
            pass

    async def reader() -> None:
        while True:
            msg = await ws.receive_json()
            if not isinstance(msg, dict):
                continue
            t = msg.get("type")
            symbol = str(msg.get("symbol", "")).upper()
            tf = str(msg.get("interval", ""))
            if tf not in INTERVALS or not symbol or symbol not in engine.market.symbols:
                continue
            if t == "chart.sub":
                await sub(symbol, tf)
            elif t == "chart.unsub":
                await unsub(symbol, tf)

    async def writer() -> None:
        await ws.send_text(dumps({"type": "status", "data": await engine.status()}))
        await ws.send_text(dumps({"type": "positions", "data": [p.model_dump(mode="json") for p in engine.positions.values()]}))
        while True:
            m = await q.get()
            if m["type"] == "kline":
                d = m["data"]
                if (d["symbol"], d["interval"]) not in subs:
                    continue
            await ws.send_text(dumps(m))

    tasks = [asyncio.create_task(reader()), asyncio.create_task(writer())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                log.debug("WS oturumu kapandı: %s", exc)
    finally:
        ctx.bus.unsubscribe(q)
        for key in list(subs):
            await unsub(*key)
