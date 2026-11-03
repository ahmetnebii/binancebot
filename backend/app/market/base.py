"""Piyasa verisi arayüzü ve mum tamponu."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import numpy as np

from ..models import Candle, SymbolInfo
from ..util import now_ms, tf_ms

log = logging.getLogger(__name__)

BarCloseHandler = Callable[[str, str, Candle], Awaitable[None]]
KlineListener = Callable[[str, str, Candle], None]


@dataclass
class Ticker:
    last: float
    open: float
    high: float
    low: float
    quote_volume: float
    change_pct: float
    ts: int


@dataclass
class MarkInfo:
    mark: float
    index: float
    funding_rate: float
    next_funding_time: int
    ts: int


class CandleBuffer:
    """Bir sembol ve zaman dilimi için kapanmış mumlar + oluşmakta olan mum."""

    def __init__(self, tf: str, maxlen: int = 700):
        self.tf = tf
        self.step = tf_ms(tf)
        self.closed: deque[Candle] = deque(maxlen=maxlen)
        self.current: Optional[Candle] = None
        self._version = 0
        self._cache_version = -1
        self._cache: dict[str, np.ndarray] = {}
        self.gap_detected = False

    def __len__(self) -> int:
        return len(self.closed)

    def load(self, candles: list[Candle]) -> None:
        self.closed.clear()
        self.current = None
        for c in sorted(candles, key=lambda x: x.t):
            if c.closed:
                if self.closed and self.closed[-1].t == c.t:
                    self.closed[-1] = c
                else:
                    self.closed.append(c)
            else:
                self.current = c
        self._version += 1

    def merge_closed(self, candles: list[Candle]) -> None:
        """Boşluk doldurma: REST'ten gelen kapanmış mumları sıralı biçimde birleştirir."""
        by_t = {c.t: c for c in self.closed}
        for c in candles:
            if c.closed:
                by_t[c.t] = c
        maxlen = self.closed.maxlen
        self.closed = deque(sorted(by_t.values(), key=lambda x: x.t), maxlen=maxlen)
        self.gap_detected = False
        self._version += 1

    def update(self, c: Candle) -> Optional[Candle]:
        """Yeni mum verisini işler. Bir bar kapandıysa kapanan mumu döndürür."""
        last_closed_t = self.closed[-1].t if self.closed else None
        if c.closed:
            if last_closed_t is not None and c.t < last_closed_t:
                return None
            if last_closed_t is not None and c.t == last_closed_t:
                self.closed[-1] = c
                self._version += 1
                return None
            if last_closed_t is not None and c.t > last_closed_t + self.step:
                self.gap_detected = True
            self.closed.append(c)
            if self.current is not None and self.current.t <= c.t:
                self.current = None
            self._version += 1
            return c
        # oluşmakta olan mum
        if last_closed_t is not None and c.t <= last_closed_t:
            return None
        closed_out: Optional[Candle] = None
        if self.current is not None and c.t > self.current.t:
            # kapanış olayı kaçırıldı: önceki mumu kapanmış kabul et
            prev = self.current
            prev.closed = True
            if last_closed_t is None or prev.t > last_closed_t:
                self.closed.append(prev)
                self._version += 1
                closed_out = prev
        self.current = c
        return closed_out

    def last_closed(self) -> Optional[Candle]:
        return self.closed[-1] if self.closed else None

    def arrays(self) -> dict[str, np.ndarray]:
        if self._cache_version != self._version:
            cs = list(self.closed)
            self._cache = {
                "t": np.fromiter((c.t for c in cs), dtype=np.int64, count=len(cs)),
                "o": np.fromiter((c.o for c in cs), dtype=float, count=len(cs)),
                "h": np.fromiter((c.h for c in cs), dtype=float, count=len(cs)),
                "l": np.fromiter((c.l for c in cs), dtype=float, count=len(cs)),
                "c": np.fromiter((c.c for c in cs), dtype=float, count=len(cs)),
                "v": np.fromiter((c.v for c in cs), dtype=float, count=len(cs)),
                "qv": np.fromiter((c.qv for c in cs), dtype=float, count=len(cs)),
                "tbq": np.fromiter((c.tbq for c in cs), dtype=float, count=len(cs)),
            }
            self._cache_version = self._version
        return self._cache

    def chart(self, limit: int) -> list[Candle]:
        out = list(self.closed)[-limit:]
        if self.current is not None and (not out or self.current.t > out[-1].t):
            out.append(self.current)
        return out[-limit:]


class MarketData:
    """Tüm piyasa veri kaynaklarının ortak arayüzü."""

    source = "base"
    base_tfs: tuple[str, ...] = ("15m", "1h")

    def __init__(self) -> None:
        self.symbols: dict[str, SymbolInfo] = {}
        self.universe: list[str] = []
        self.tickers: dict[str, Ticker] = {}
        self.marks: dict[str, MarkInfo] = {}
        self.buffers: dict[tuple[str, str], CandleBuffer] = {}
        self.bar_close_handlers: list[BarCloseHandler] = []
        self.kline_listeners: list[KlineListener] = []
        self.ready = asyncio.Event()
        self.state = "connecting"
        self.state_message = ""
        self.last_msg_ts = 0

    # --- erişimciler ---------------------------------------------------
    def price(self, symbol: str) -> Optional[float]:
        t = self.tickers.get(symbol)
        m = self.marks.get(symbol)
        if t and (not m or t.ts >= m.ts - 5000):
            return t.last
        if m:
            return m.mark
        return t.last if t else None

    def mark(self, symbol: str) -> Optional[float]:
        m = self.marks.get(symbol)
        if m:
            return m.mark
        t = self.tickers.get(symbol)
        return t.last if t else None

    def funding(self, symbol: str) -> Optional[MarkInfo]:
        return self.marks.get(symbol)

    def buffer(self, symbol: str, tf: str) -> Optional[CandleBuffer]:
        return self.buffers.get((symbol, tf))

    def volume_rank(self, symbol: str) -> int:
        """24 saatlik USDT hacmine göre sıra (1 = en yüksek)."""
        vols = sorted(((t.quote_volume, s) for s, t in self.tickers.items()), reverse=True)
        for i, (_, s) in enumerate(vols, start=1):
            if s == symbol:
                return i
        return 9999

    def status(self) -> dict:
        age = (now_ms() - self.last_msg_ts) if self.last_msg_ts else None
        return {
            "source": self.source,
            "state": self.state,
            "message": self.state_message,
            "last_msg_age_ms": age,
            "symbols": len(self.symbols),
            "universe": len(self.universe),
        }

    # --- olay yayını ---------------------------------------------------
    async def _emit_bar_close(self, symbol: str, tf: str, candle: Candle) -> None:
        for h in list(self.bar_close_handlers):
            try:
                await h(symbol, tf, candle)
            except Exception:  # pragma: no cover - savunmacı
                log.exception("Bar kapanış işleyicisi hata verdi (%s %s)", symbol, tf)

    def _emit_kline(self, symbol: str, tf: str, candle: Candle) -> None:
        for fn in list(self.kline_listeners):
            try:
                fn(symbol, tf, candle)
            except Exception:  # pragma: no cover
                log.exception("Kline dinleyicisi hata verdi")

    # --- alt sınıfların uygulayacakları ---------------------------------
    async def start(self) -> None:  # pragma: no cover - soyut
        raise NotImplementedError

    async def stop(self) -> None:  # pragma: no cover - soyut
        raise NotImplementedError

    async def klines(self, symbol: str, tf: str, limit: int = 300) -> list[Candle]:  # pragma: no cover
        raise NotImplementedError

    async def watch(self, symbol: str, tf: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def unwatch(self, symbol: str, tf: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def book(self, symbol: str) -> Optional[tuple[float, float]]:
        """En iyi alış/satış fiyatı. Bilinmiyorsa None."""
        return None

    async def oi_change_pct(self, symbol: str, minutes: int = 60) -> Optional[float]:
        return None
