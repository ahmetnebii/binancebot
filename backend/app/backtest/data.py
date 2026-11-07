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
from typing import Callable, Optional

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

    # --- önbellek -----------------------------------------------------------
    # Her dosya, verinin hangi tarihten itibaren İSTENDİĞİNİ de saklar ("req_from"). Böylece 3 yıldan yeni
    # listelenmiş bir coinde "baş kısım eksik" sanılıp her seferinde baştan indirme yapılmaz.
    def _read_cache(self, path: Path, fields: tuple[str, ...]) -> tuple[dict[str, np.ndarray], Optional[int]]:
        if not path.exists():
            return {k: np.array([], dtype=np.int64 if k == "t" else float) for k in fields}, None
        try:
            with np.load(path) as z:
                data = {k: z[k] for k in fields}
                req = int(z["req_from"][0]) if "req_from" in z.files else None
            return data, req
        except Exception:
            return {k: np.array([], dtype=np.int64 if k == "t" else float) for k in fields}, None

    @staticmethod
    def _write_cache(path: Path, data: dict[str, np.ndarray], req_from: Optional[int]) -> None:
        tmp = path.with_suffix(".tmp.npz")
        extra = {"req_from": np.array([req_from if req_from is not None else -1], dtype=np.int64)}
        np.savez_compressed(tmp, **data, **extra)
        tmp.replace(path)

    def _covers_start(self, cached_t: np.ndarray, req: Optional[int], start: int) -> bool:
        if len(cached_t) and int(cached_t[0]) <= start + STEP:
            return True
        return req is not None and req >= 0 and req <= start

    async def _load_symbol(self, session: aiohttp.ClientSession, symbol: str, start: int, end: int,
                           update_tail: bool = True) -> dict[str, np.ndarray]:
        path = self.cache_dir / f"{symbol}_15m.npz"
        cached, req = self._read_cache(path, FIELDS)
        have = len(cached["t"]) > 0
        head_ok = have and self._covers_start(cached["t"], req, start)
        tail_ok = have and int(cached["t"][-1]) >= end - 2 * STEP
        if head_ok and (tail_ok or not update_tail):
            return _slice(cached, start, end)
        new_req = min(start, req) if (req is not None and req >= 0) else start
        if head_ok:
            fresh = await self._fetch_klines(session, symbol, int(cached["t"][-1]) + STEP, end)
            data = _merge(cached, fresh)
            new_req = req if (req is not None and req >= 0) else int(cached["t"][0])
        else:
            data = await self._fetch_klines(session, symbol, start, end)
            if have:
                data = _merge(data, cached)
        if len(data["t"]):
            self._write_cache(path, data, new_req)
        return _slice(data, start, end)

    async def _load_funding(self, session: aiohttp.ClientSession, symbol: str, start: int, end: int,
                            update_tail: bool = True) -> tuple[np.ndarray, np.ndarray]:
        path = self.cache_dir / f"{symbol}_funding.npz"
        cached, req = self._read_cache(path, ("t", "r"))
        have = len(cached["t"]) > 0
        head_ok = have and self._covers_start(cached["t"], req, start + 8 * 3_600_000)
        tail_ok = have and int(cached["t"][-1]) >= end - 9 * 3_600_000
        if head_ok and (tail_ok or not update_tail):
            m = (cached["t"] >= start) & (cached["t"] < end)
            return cached["t"][m], cached["r"][m]
        cur = int(cached["t"][-1]) + 1 if head_ok else start
        times: list[int] = []
        rates: list[float] = []
        for _ in range(60):
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
        t = np.concatenate([cached["t"], np.array(times, dtype=np.int64)]) if head_ok else np.array(times, dtype=np.int64)
        r = np.concatenate([cached["r"], np.array(rates, dtype=float)]) if head_ok else np.array(rates, dtype=float)
        if not head_ok and have:
            t = np.concatenate([t, cached["t"]])
            r = np.concatenate([r, cached["r"]])
        order = np.argsort(t, kind="stable")
        t, r = t[order], r[order]
        keep = np.r_[t[1:] != t[:-1], True] if len(t) else np.array([], dtype=bool)
        t, r = t[keep], r[keep]
        new_req = min(start, req) if (req is not None and req >= 0) else start
        if len(t):
            self._write_cache(path, {"t": t, "r": r}, new_req)
        m = (t >= start) & (t < end)
        return t[m], r[m]

    def cache_status(self) -> dict:
        """Önbellekteki geçmiş verinin özeti (arayüzdeki "Veri" satırı için)."""
        files = sorted(self.cache_dir.glob("*_15m.npz"))
        first, last, updated, size, n = None, None, None, 0, 0
        for f in files:
            try:
                with np.load(f) as z:
                    t = z["t"]
                    if len(t) == 0:
                        continue
                    n += 1
                    first = int(t[0]) if first is None else min(first, int(t[0]))
                    last = int(t[-1]) if last is None else max(last, int(t[-1]))
            except Exception:
                continue
            st = f.stat()
            size += st.st_size
            updated = int(st.st_mtime * 1000) if updated is None else max(updated, int(st.st_mtime * 1000))
        for f in self.cache_dir.glob("*_funding.npz"):
            size += f.stat().st_size
        return {"symbols": n, "first": first, "last": (last + STEP) if last is not None else None,
                "updated_at": updated, "size_mb": round(size / 1_048_576, 1)}

    async def load(self, symbols: list[str], start: int, end: int, progress: Progress, cancelled: Cancelled,
                   update_tail: bool = True, funding_only: bool = False) -> HistoryData:
        """update_tail=False: önbellek başlangıcı kapsıyorsa ağa hiç çıkılmaz (strateji araması böyle çalışır).
        funding_only=True: yalnızca funding geçmişi (mum indirilmez; funding arbitrajı analizi için yeterli ve çok hızlı)."""
        out = HistoryData(source="binance")
        verb = "indiriliyor" if update_tail else "yükleniyor"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            for i, sym in enumerate(symbols):
                if cancelled():
                    raise BacktestCancelled()
                if funding_only:
                    progress(i / max(1, len(symbols)), f"{sym} funding geçmişi alınıyor ({i + 1}/{len(symbols)})")
                    try:
                        f = await self._load_funding(session, sym, start, end, update_tail)
                    except Exception as exc:
                        log.info("%s funding geçmişi alınamadı: %s", sym, exc)
                        out.missing.append(sym)
                        continue
                    if len(f[0]):
                        out.funding[sym] = f
                    else:
                        out.missing.append(sym)
                    continue
                progress(i / max(1, len(symbols)), f"{sym} mumları {verb} ({i + 1}/{len(symbols)})")
                try:
                    bars = await self._load_symbol(session, sym, start, end, update_tail)
                except Exception as exc:
                    log.warning("%s geçmiş verisi alınamadı: %s", sym, exc)
                    out.missing.append(sym)
                    continue
                if len(bars["t"]) < 2000:
                    out.missing.append(sym)
                    continue
                out.bars[sym] = bars
                try:
                    out.funding[sym] = await self._load_funding(session, sym, start, end, update_tail)
                except Exception as exc:
                    log.info("%s funding geçmişi alınamadı: %s", sym, exc)
        progress(1.0, "Veri hazır")
        return out


class SyntheticHistory:
    """YALNIZCA GELİŞTİRME: trend/yatay dönemleri olan sentetik 15 dk geçmiş."""

    def __init__(self, seed: int = 21):
        self.seed = seed

    async def load(self, symbols: list[str], start: int, end: int, progress: Progress, cancelled: Cancelled,
                   update_tail: bool = True, funding_only: bool = False) -> HistoryData:
        return await asyncio.to_thread(self._generate, symbols, start, end, progress, cancelled)

    def cache_status(self) -> dict:
        return {"symbols": 0, "first": None, "last": None, "updated_at": None, "size_mb": 0.0, "synthetic": True}

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
            # gerçeğe benzer funding: coin'e özgü ortalama + kalıcı (AR(1)) sapmalar + ara sıra aşırı dönemler
            mu = float(rng.normal(0.0001, 0.00008))
            fr = np.empty(len(ft))
            x = 0.0
            for j in range(len(ft)):
                x = 0.97 * x + float(rng.normal(0.0, 0.00004))
                fr[j] = mu + x
            spikes_f = rng.random(len(ft)) < 0.01
            fr[spikes_f] += rng.normal(0.0, 0.0008, int(spikes_f.sum()))
            out.funding[sym] = (ft, np.clip(fr, -0.003, 0.003))
            progress((i + 1) / len(symbols), f"{sym} sentetik geçmiş üretildi")
        return out
