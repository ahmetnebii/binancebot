"""Süreç içi olay yolu (arayüz WebSocket'ine yayın) ve kalıcı olay günlüğü."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from .db import Database
from .util import now_ms

logger = logging.getLogger("binancebot")

_LEVEL_MAP = {"info": logging.INFO, "success": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self, maxsize: int = 1000) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def publish(self, type_: str, data: Any) -> None:
        if not self._subs:
            return
        msg = {"type": type_, "data": data}
        for q in list(self._subs):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass


class EventLog:
    def __init__(self, db: Database, bus: EventBus):
        self.db = db
        self.bus = bus

    async def log(
        self,
        level: str,
        kind: str,
        message: str,
        data: Any = None,
        toast: bool = False,
        title: Optional[str] = None,
    ) -> None:
        ts = now_ms()
        logger.log(_LEVEL_MAP.get(level, logging.INFO), "[%s] %s", kind, message)
        try:
            eid = await self.db.add_event(ts, level, kind, message, data)
        except Exception:  # pragma: no cover - DB kapanırken
            eid = 0
        self.bus.publish("event", {"id": eid, "ts": ts, "level": level, "kind": kind, "message": message})
        if toast:
            self.toast(level, title or kind, message)

    def toast(self, level: str, title: str, message: str) -> None:
        self.bus.publish("toast", {"level": level, "title": title, "message": message})
