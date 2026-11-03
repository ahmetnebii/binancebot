from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Database  # noqa: E402
from app.market.base import MarketData, MarkInfo  # noqa: E402
from app.models import SymbolInfo  # noqa: E402
from app.security import SecretBox  # noqa: E402
from app.settings import SettingsStore  # noqa: E402


class FakeMarket(MarketData):
    """Testler için elle fiyat verilen piyasa."""

    source = "fake"

    def __init__(self) -> None:
        super().__init__()
        self._p: dict[str, float] = {}
        self._m: dict[str, float] = {}

    def add_symbol(self, symbol: str, tick: str = "0.01", step: str = "0.001", min_notional: str = "5") -> None:
        self.symbols[symbol] = SymbolInfo(
            symbol=symbol, base=symbol[:-4], tick_size=tick, step_size=step, market_step_size=step,
            min_qty=step, min_notional=min_notional,
        )

    def set(self, symbol: str, price: float, mark: Optional[float] = None) -> None:
        self._p[symbol] = price
        self._m[symbol] = mark if mark is not None else price

    def set_funding(self, symbol: str, rate: float, next_time: int) -> None:
        m = self._m.get(symbol, 0.0)
        self.marks[symbol] = MarkInfo(mark=m, index=m, funding_rate=rate, next_funding_time=next_time, ts=0)

    def price(self, symbol: str) -> Optional[float]:
        return self._p.get(symbol)

    def mark(self, symbol: str) -> Optional[float]:
        return self._m.get(symbol)

    async def start(self) -> None:
        self.ready.set()

    async def stop(self) -> None:
        return None


@pytest.fixture
async def db(tmp_path):
    d = Database(tmp_path / "test.sqlite3")
    await d.open()
    yield d
    await d.close()


@pytest.fixture
async def store(db, tmp_path):
    s = SettingsStore(db, SecretBox(tmp_path))
    await s.load()
    return s


@pytest.fixture
def market() -> FakeMarket:
    m = FakeMarket()
    m.add_symbol("BTCUSDT", tick="0.1", step="0.001")
    m.add_symbol("ETHUSDT", tick="0.01", step="0.001")
    m.add_symbol("SOLUSDT", tick="0.01", step="0.01")
    m.set("BTCUSDT", 100.0)
    m.set("ETHUSDT", 100.0)
    m.set("SOLUSDT", 100.0)
    return m
