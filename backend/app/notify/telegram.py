"""Telegram bildirimleri ve uzaktan komutlar (Telegram Bot API ücretsizdir)."""
from __future__ import annotations

import asyncio
import html
import logging
from typing import Awaitable, Callable, Optional

import aiohttp

from ..settings import SettingsStore

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
CommandHandler = Callable[[list[str]], Awaitable[str]]

HELP = (
    "<b>binancebot komutları</b>\n"
    "/durum — sistem durumu\n"
    "/pozisyonlar — açık pozisyonlar\n"
    "/otomatik_ac — otomatik modu aç\n"
    "/otomatik_kapat — otomatik modu kapat\n"
    "/durdur — yeni işlemleri durdur\n"
    "/devam — işlemlere devam\n"
    "/yardim — bu mesaj"
)


def esc(s: object) -> str:
    return html.escape(str(s), quote=False)


class TelegramNotifier:
    def __init__(self, store: SettingsStore):
        self.store = store
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        self._session: Optional[aiohttp.ClientSession] = None
        self._tasks: list[asyncio.Task] = []
        self._offset = 0
        self.commands: dict[str, CommandHandler] = {}
        self.last_error = ""

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        self._tasks = [
            asyncio.create_task(self._sender(), name="telegram-sender"),
            asyncio.create_task(self._poller(), name="telegram-poller"),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._session:
            await self._session.close()
            self._session = None

    def notify(self, text: str, kind: str = "system") -> None:
        cfg = self.store.telegram
        if not cfg.enabled or not cfg.chat_id:
            return
        if kind == "signal" and not cfg.notify_signals:
            return
        if kind == "trade" and not cfg.notify_trades:
            return
        try:
            self._queue.put_nowait(text)
        except asyncio.QueueFull:
            pass

    async def _sender(self) -> None:
        while True:
            text = await self._queue.get()
            ok, msg = await self.send(text)
            if not ok:
                self.last_error = msg
                log.warning("Telegram mesajı gönderilemedi: %s", msg)
            await asyncio.sleep(1.1)

    async def send(self, text: str, chat_id: Optional[str] = None) -> tuple[bool, str]:
        token = await self.store.get_secret("telegram_bot_token")
        chat = chat_id or self.store.telegram.chat_id
        if not token:
            return False, "Bot token kayıtlı değil"
        if not chat:
            return False, "Chat ID girilmemiş"
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        try:
            async with self._session.post(
                f"{API}/bot{token}/sendMessage",
                json={"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            ) as r:
                data = await r.json(content_type=None)
                if r.status == 200 and isinstance(data, dict) and data.get("ok"):
                    return True, "Mesaj gönderildi"
                desc = data.get("description") if isinstance(data, dict) else None
                return False, desc or f"HTTP {r.status}"
        except Exception as exc:
            return False, f"Telegram'a ulaşılamadı: {exc.__class__.__name__}"

    async def discover_chats(self) -> list[dict]:
        token = await self.store.get_secret("telegram_bot_token")
        if not token:
            raise ValueError("Önce bot token'ını kaydedin")
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        async with self._session.get(f"{API}/bot{token}/getUpdates", params={"timeout": 0}) as r:
            data = await r.json(content_type=None)
        if not isinstance(data, dict) or not data.get("ok"):
            raise ValueError((data or {}).get("description", "Telegram yanıt vermedi"))
        seen: dict[str, str] = {}
        for upd in data.get("result", []):
            msg = upd.get("message") or upd.get("channel_post") or {}
            chat = msg.get("chat") or {}
            cid = str(chat.get("id", ""))
            if not cid:
                continue
            title = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username") or cid
            seen[cid] = str(title)
        return [{"chat_id": k, "title": v} for k, v in seen.items()]

    async def _poller(self) -> None:
        while True:
            cfg = self.store.telegram
            token = await self.store.get_secret("telegram_bot_token")
            if not (cfg.enabled and cfg.chat_id and token and self._session):
                await asyncio.sleep(10)
                continue
            try:
                async with self._session.get(
                    f"{API}/bot{token}/getUpdates",
                    params={"timeout": 25, "offset": self._offset},
                    timeout=aiohttp.ClientTimeout(total=40),
                ) as r:
                    data = await r.json(content_type=None)
                for upd in (data or {}).get("result", []):
                    self._offset = int(upd.get("update_id", 0)) + 1
                    msg = upd.get("message") or {}
                    chat = str((msg.get("chat") or {}).get("id", ""))
                    text = str(msg.get("text", "")).strip()
                    if chat != cfg.chat_id or not text.startswith("/"):
                        continue
                    parts = text.split()
                    cmd = parts[0].split("@")[0].lower()
                    handler = self.commands.get(cmd)
                    reply = await handler(parts[1:]) if handler else HELP
                    await self.send(reply)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("Telegram komut okuma hatası: %s", exc)
                await asyncio.sleep(5)
