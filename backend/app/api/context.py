"""Uygulama bağlamı: tüm bileşenleri kurar ve yaşam döngüsünü yönetir."""
from __future__ import annotations

import logging
from typing import Optional

from ..db import Database
from ..engine.engine import Engine
from ..events import EventBus, EventLog
from ..market.base import MarketData
from ..market.binance import BinanceMarketData
from ..market.synthetic import SyntheticMarketData
from ..notify.telegram import TelegramNotifier
from ..paths import data_dir, data_source
from ..risk.manager import RiskManager
from ..security import LoginLimiter, SecretBox
from ..settings import SettingsStore

log = logging.getLogger(__name__)


class AppContext:
    def __init__(self) -> None:
        self.data_dir = data_dir()
        self.db = Database(self.data_dir / "binancebot.sqlite3")
        self.box = SecretBox(self.data_dir)
        self.store = SettingsStore(self.db, self.box)
        self.bus = EventBus()
        self.events = EventLog(self.db, self.bus)
        self.risk = RiskManager(self.store, self.db)
        self.notifier = TelegramNotifier(self.store)
        self.limiter = LoginLimiter()
        self.synthetic = data_source() == "synthetic"
        self.engine: Optional[Engine] = None
        self._market: Optional[MarketData] = None
        self._market_key: Optional[tuple] = None

    def market_factory(self, env: str) -> MarketData:
        """Ortama uygun piyasa verisi kaynağı; adresler değişmediyse mevcut kaynak korunur."""
        if self.synthetic:
            if self._market is None:
                self._market = SyntheticMarketData()
            return self._market
        conn = self.store.connection
        if env == "testnet":
            rest, ws = conn.testnet_rest, conn.testnet_ws
        else:
            rest, ws = conn.live_rest, conn.live_ws
        key = (rest, ws, conn.market_stream_path)
        if self._market is None or self._market_key != key:
            self._market = BinanceMarketData(rest, ws, conn.market_stream_path, lambda: self.store.scanner, self.events)
            self._market_key = key
        return self._market

    async def start(self) -> None:
        await self.db.open()
        await self.store.load()
        if self.synthetic and self.store.connection.env != "paper":
            log.warning("Sentetik veri modunda yalnızca simülasyon ortamı kullanılabilir")
        self.engine = Engine(
            db=self.db, store=self.store, bus=self.bus, events=self.events, risk=self.risk,
            notifier=self.notifier, market_factory=self.market_factory, synthetic=self.synthetic,
        )
        await self.notifier.start()
        await self.engine.start()
        mode = "SENTETİK GELİŞTİRME VERİSİ" if self.synthetic else "Binance verisi"
        await self.events.log("info", "system", f"binancebot başlatıldı ({mode}, ortam: {self.engine.env})")

    async def stop(self) -> None:
        if self.engine:
            await self.engine.stop()
        await self.notifier.stop()
        await self.db.close()
