"""Binance Spot'ta işlem gören coinler (funding arbitrajının spot bacağı için).

Delta-nötr funding stratejisi coinin spotta alınabilmesini gerektirir. Vadelide "1000PEPEUSDT" gibi
çarpanlı semboller spotta "PEPEUSDT" olarak işlem görür; bu eşleme burada yapılır.
Liste günde bir kez yenilenir ve veritabanında saklanır (ağ yoksa son liste kullanılır).
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Optional

import aiohttp

from ..util import now_ms

if TYPE_CHECKING:  # pragma: no cover
    from ..db import Database

log = logging.getLogger(__name__)
SPOT_REST = "https://api.binance.com"
_MULT = re.compile(r"^(1000000|100000|10000|1000|1M)(?=[A-Z])")


def spot_base(futures_symbol: str) -> str:
    """'1000PEPEUSDT' → 'PEPE', 'BTCUSDT' → 'BTC'."""
    base = futures_symbol[:-4] if futures_symbol.endswith("USDT") else futures_symbol
    return _MULT.sub("", base)


async def fetch_spot_bases(session: aiohttp.ClientSession, rest: str = SPOT_REST) -> set[str]:
    async with session.get(rest.rstrip("/") + "/api/v3/exchangeInfo", params={"permissions": "SPOT"}) as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
    return {s["baseAsset"] for s in data.get("symbols", []) if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"}


async def spot_bases(db: "Database", synthetic: bool = False, max_age_ms: int = 86_400_000) -> Optional[set[str]]:
    """Spotta USDT çiftiyle işlem gören coinler. Sentetik modda None (filtre uygulanmaz)."""
    if synthetic:
        return None
    cached = await db.kv_get("spot_bases", None)
    if cached and now_ms() - int(cached.get("ts", 0)) < max_age_ms:
        return set(cached["bases"])
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
            bases = await fetch_spot_bases(s)
        if bases:
            await db.kv_set("spot_bases", {"ts": now_ms(), "bases": sorted(bases)}, now_ms())
            return bases
    except Exception as exc:
        log.warning("Spot coin listesi alınamadı: %s", exc)
    return set(cached["bases"]) if cached else None


def has_spot(futures_symbol: str, bases: Optional[set[str]]) -> bool:
    return True if bases is None else spot_base(futures_symbol) in bases


# --- diğer borsaların spot listeleri (çapraz borsa funding arbitrajı) ----------------------
async def _bybit(session: aiohttp.ClientSession) -> set[str]:
    out: set[str] = set()
    cursor = ""
    for _ in range(10):
        params = {"category": "spot", "limit": "1000"}
        if cursor:
            params["cursor"] = cursor
        async with session.get("https://api.bybit.com/v5/market/instruments-info", params=params) as r:
            r.raise_for_status()
            data = await r.json(content_type=None)
        res = (data or {}).get("result") or {}
        for it in res.get("list") or []:
            if it.get("quoteCoin") == "USDT" and it.get("status") == "Trading":
                out.add(it.get("baseCoin", ""))
        cursor = res.get("nextPageCursor") or ""
        if not cursor:
            break
    return out


async def _okx(session: aiohttp.ClientSession) -> set[str]:
    async with session.get("https://www.okx.com/api/v5/public/instruments", params={"instType": "SPOT"}) as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
    return {it["baseCcy"] for it in (data or {}).get("data") or [] if it.get("quoteCcy") == "USDT" and it.get("state") == "live"}


async def _gate(session: aiohttp.ClientSession) -> set[str]:
    async with session.get("https://api.gateio.ws/api/v4/spot/currency_pairs") as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
    return {it["base"] for it in data or [] if it.get("quote") == "USDT" and it.get("trade_status") == "tradable"}


async def spot_bases_multi(db: "Database", synthetic: bool = False, max_age_ms: int = 86_400_000) -> Optional[dict[str, set[str]]]:
    """Borsa → spotta USDT çiftiyle işlem gören coinler (Binance, Bybit, OKX, Gate). Sentetik modda None."""
    if synthetic:
        return None
    cached = await db.kv_get("spot_bases_multi", None)
    if cached and now_ms() - int(cached.get("ts", 0)) < max_age_ms:
        return {k: set(v) for k, v in cached["bases"].items()}
    out: dict[str, set[str]] = {}
    binance = await spot_bases(db, synthetic)
    if binance:
        out["Binance"] = binance
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as s:
        for name, fn in (("Bybit", _bybit), ("OKX", _okx), ("Gate", _gate)):
            try:
                b = await fn(s)
                if b:
                    out[name] = b
            except Exception as exc:  # bir borsaya ulaşılamazsa diğerleriyle devam
                log.warning("%s spot listesi alınamadı: %s", name, exc)
    if out:
        await db.kv_set("spot_bases_multi", {"ts": now_ms(), "bases": {k: sorted(v) for k, v in out.items()}}, now_ms())
        return out
    return {k: set(v) for k, v in cached["bases"].items()} if cached else None
