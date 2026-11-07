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
