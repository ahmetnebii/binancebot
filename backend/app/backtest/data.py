"""Backtest için geçmiş veri.

- BinanceHistory: 15 dakikalık mumlar ve funding geçmişi Binance REST'ten çekilir,
  `data/history` altında sıkıştırılmış .npz olarak önbelleğe alınır (sonraki testler hızlı).
  Canlı sistemi etkilememek için düşük bir ağırlık bütçesiyle (dakikada ~900) çalışır.
- SyntheticHistory: YALNIZCA geliştirme/test içindir; gerçek piyasa değildir.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import aiohttp
import numpy as np

from ..market.synthetic import SPECS
from ..util import now_ms

log = logging.getLogger(__name__)

STEP = 900_000  # 15 dk
FIELDS = ("t", "o", "h", "l", "c", "v", "qv", "tbq")
Progress = Callable[[float, str], None]
Cancelled = Callable[[], bool]


class BacktestCancelled(Exception):
    pass


@dataclass
class HistoryData:
    bars: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)  # sembol → 15 dk diziler
    funding: dict[str, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)  # (zaman ms, oran)
    source: str = "binance"
    missing: list[str] = field(default_factory=list)


def _empty() -> dict[str, np.ndarray]:
    return {k: np.array([], dtype=np.int64 if k == "t" else float) for k in FIELDS}


def _rows_to_arrays(rows: list) -> dict[str, np.ndarray]:
    if not rows:
        return _empty()
    return {
        "t": np.array([int(r[0]) for r in rows], dtype=np.int64),
        "o": np.array([float(r[1]) for r in rows]),
        "h": np.array([float(r[2]) for r in rows]),
        "l": np.array([float(r[3]) for r in rows]),
        "c": np.array([float(r[4]) for r in rows]),
        "v": np.array([float(r[5]) for r in rows]),
        "qv": np.array([float(r[7]) for r in rows]),
        "tbq": np.array([float(r[10]) for r in rows]),
    }


def _merge(a: dict[str, np.ndarray], b: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    if len(a["t"]) == 0:
        return b
    if len(b["t"]) == 0:
        return a
    t = np.concatenate([a["t"], b["t"]])
    order = np.argsort(t, kind="stable")
    t_sorted = t[order]
    keep = np.r_[t_sorted[1:] != t_sorted[:-1], True]  # tekrarlarda son geleni tut
    out = {}
    for k in FIELDS:
        merged = np.concatenate([a[k], b[k]])[order]
        out[k] = merged[keep]
    return out


def _slice(a: dict[str, np.ndarray], start: int, end: int) -> dict[str, np.ndarray]:
    i = int(np.searchsorted(a["t"], start, side="left"))
    j = int(np.searchsorted(a["t"], end, side="left"))
    return {k: v[i:j] for k, v in a.items()}


class BinanceHistory:
    def __init__(self, rest_base: str, cache_dir: Path, weight_per_min: int = 900):
        self.rest_base = rest_base.rstrip("/")
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.weight_per_min = weight_per_min
        self._next_ok = 0.0

    async def _get(self, session: aiohttp.ClientSession, path: str, params: dict, weight: int):
        for attempt in range(4):
            loop = asyncio.get_running_loop()
            wait = self._next_ok - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_ok = max(self._next_ok, loop.time()) + weight * 60.0 / self.weight_per_min
            async with session.get(self.rest_base + path, params=params) as r:
                if r.status in (418, 429):
                    retry = int(r.headers.get("Retry-After", "60") or 60)
                    log.warning("Backtest veri indirme hız limiti; %s sn bekleniyor", retry)
                    await asyncio.sleep(retry + 1)
                    continue
                if r.status >= 500:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                if r.status >= 400:
                    raise RuntimeError(f"Binance REST {r.status}: {(await r.text())[:160]}")
                return await r.json(content_type=None)
        raise RuntimeError("Binance verisi alınamadı (tekrar denemeler tükendi)")

    async def _fetch_klines(self, session: aiohttp.ClientSession, symbol: str, start: int, end: int) -> dict[str, np.ndarray]:
        out = _empty()
        cur = start
        guard = 0
        while cur < end and guard < 1000:
            guard += 1
            rows = await self._get(
                session, "/fapi/v1/klines",
                {"symbol": symbol, "interval": "15m", "startTime": cur, "endTime": end - 1, "limit": 1500}, weight=10,
            )
            if not rows:
                break
            closed = [r for r in rows if int(r[6]) < now_ms()]
            out = _merge(out, _rows_to_arrays(closed))
            nxt = int(rows[-1][0]) + STEP
            if nxt <= cur:
                break
            cur = nxt
        return out

    async def _load_symbol(self, session: aiohttp.ClientSession, symbol: str, start: int, end: int) -> dict[str, np.ndarray]:
        path = self.cache_dir / f"{symbol}_15m.npz"
        cached = _empty()
        if path.exists():
            try:
                with np.load(path) as z:
                    cached = {k: z[k] for k in FIELDS}
            except Exception:
                cached = _empty()
        have = len(cached["t"]) > 0
        if have and int(cached["t"][0]) <= start + STEP and int(cached["t"][-1]) >= end - 2 * STEP:
            return _slice(cached, start, end)
        if have and int(cached["t"][0]) <= start + STEP:
            fresh = await self._fetch_klines(session, symbol, int(cached["t"][-1]) + STEP, end)
            data = _merge(cached, fresh)
        else:
            data = await self._fetch_klines(session, symbol, start, end)
            if have:
                data = _merge(data, cached)
        if len(data["t"]):
            tmp = path.with_suffix(".tmp.npz")
            np.savez_compressed(tmp, **data)
            tmp.replace(path)
        return _slice(data, start, end)

    async def _load_funding(self, session: aiohttp.ClientSession, symbol: str, start: int, end: int) -> tuple[np.ndarray, np.ndarray]:
        times: list[int] = []
        rates: list[float] = []
        cur = start
        for _ in range(20):
            rows = await self._get(
                session, "/fapi/v1/fundingRate", {"symbol": symbol, "startTime": cur, "endTime": end, "limit": 1000}, weight=1,
            )
            if not rows:
                break
            for r in rows:
                times.append(int(r["fundingTime"]))
                rates.append(float(r["fundingRate"]))
            if len(rows) < 1000:
                break
            cur = int(rows[-1]["fundingTime"]) + 1
        return np.array(times, dtype=np.int64), np.array(rates, dtype=float)

    async def load(self, symbols: list[str], start: int, end: int, progress: Progress, cancelled: Cancelled) -> HistoryData:
        out = HistoryData(source="binance")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            for i, sym in enumerate(symbols):
                if cancelled():
                    raise BacktestCancelled()
                progress(i / max(1, len(symbols)), f"{sym} mumları indiriliyor ({i + 1}/{len(symbols)})")
                try:
                    bars = await self._load_symbol(session, sym, start, end)
                except Exception as exc:
                    log.warning("%s geçmiş verisi alınamadı: %s", sym, exc)
                    out.missing.append(sym)
                    continue
                if len(bars["t"]) < 2000:
                    out.missing.append(sym)
                    continue
                out.bars[sym] = bars
                try:
                    out.funding[sym] = await self._load_funding(session, sym, start, end)
                except Exception as exc:
                    log.info("%s funding geçmişi alınamadı: %s", sym, exc)
        progress(1.0, "Veri hazır")
        return out


class SyntheticHistory:
    """YALNIZCA GELİŞTİRME: trend/yatay dönemleri olan sentetik 15 dk geçmiş."""

    def __init__(self, seed: int = 21):
        self.seed = seed

    async def load(self, symbols: list[str], start: int, end: int, progress: Progress, cancelled: Cancelled) -> HistoryData:
        return await asyncio.to_thread(self._generate, symbols, start, end, progress, cancelled)

    def _generate(self, symbols: list[str], start: int, end: int, progress: Progress, cancelled: Cancelled) -> HistoryData:
        rng = np.random.default_rng(self.seed)
        start -= start % STEP
        n = max(10, (end - start) // STEP)
        t = start + np.arange(n, dtype=np.int64) * STEP
        specs = {s[0]: s for s in SPECS}
        common = rng.standard_normal(n)
        vol_reg = np.repeat(rng.uniform(0.7, 1.4, size=n // 192 + 1), 192)[:n]
        out = HistoryData(source="synthetic")
        for i, sym in enumerate(symbols):
            if cancelled():
                raise BacktestCancelled()
            spec = specs.get(sym)
            if spec is None:
                out.missing.append(sym)
                continue
            _, price, _, _, sigma_1m, beta_, _, qv_min, _ = spec
            sig15 = sigma_1m * math.sqrt(15)
            mu = 0.00025 if sym == "BTCUSDT" else 0.00035
            drift = np.zeros(n)
            spikes = np.ones(n)
            pos = 0
            while pos < n:
                seg = int(rng.integers(2 * 96, 8 * 96))
                drift[pos : pos + seg] = rng.choice([mu, -mu, 0.0, 0.0])
                spikes[pos : pos + 3] = rng.uniform(2.5, 5.0)
                pos += seg
            if sym == "BTCUSDT":
                r = drift + sig15 * vol_reg * common
            else:
                r = drift + beta_ * 0.0008 * math.sqrt(15) * 0.6 * vol_reg * common + sig15 * 0.8 * vol_reg * rng.standard_normal(n)
            closes = np.exp(np.cumsum(r))
            closes *= price / closes[-1]
            opens = np.r_[closes[0], closes[:-1]]
            hi = np.maximum(opens, closes) * np.exp(np.abs(rng.standard_normal(n)) * sig15 * 0.35)
            lo = np.minimum(opens, closes) * np.exp(-np.abs(rng.standard_normal(n)) * sig15 * 0.35)
            qv = qv_min * 15 * np.exp(0.35 * rng.standard_normal(n)) * (1.0 + 1.5 * np.abs(r) / sig15) * spikes
            taker = np.clip(0.5 + 0.15 * r / sig15 + 0.04 * rng.standard_normal(n), 0.15, 0.85)
            out.bars[sym] = {"t": t.copy(), "o": opens, "h": hi, "l": lo, "c": closes, "v": qv / closes, "qv": qv, "tbq": qv * taker}
            ft = np.arange(start - start % (8 * 3600_000) + 8 * 3600_000, end, 8 * 3600_000, dtype=np.int64)
            out.funding[sym] = (ft, np.full(len(ft), float(rng.normal(0.0001, 0.00006))))
            progress((i + 1) / len(symbols), f"{sym} sentetik geçmiş üretildi")
        return out
