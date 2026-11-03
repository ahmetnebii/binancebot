"""İşlem evreni (universe) seçimi."""
from __future__ import annotations

from decimal import Decimal

from ..models import SymbolInfo
from ..settings import ScannerSettings
from ..util import DAY_MS
from .base import Ticker

ALWAYS_INCLUDE = ("BTCUSDT", "ETHUSDT")


def delist_planned(info: SymbolInfo, now: int, horizon_days: int = 30) -> bool:
    """Perpetual kontratlarda deliveryDate normalde çok ileri bir tarihtir (2100).

    Yakın bir tarihe çekilmişse kontratın delist edilmesi planlanmış demektir.
    """
    return 0 < info.delivery_date < now + horizon_days * DAY_MS


def eligible(info: SymbolInfo, ticker: Ticker | None, cfg: ScannerSettings, now: int) -> tuple[bool, str]:
    if info.contract_type != "PERPETUAL":
        return False, "perpetual değil"
    if info.quote != "USDT":
        return False, "USDT dışı"
    if info.status != "TRADING":
        return False, "işleme kapalı"
    if info.symbol in cfg.blacklist:
        return False, "kara listede"
    if delist_planned(info, now):
        return False, "delist planlı"
    if info.onboard_date and now - info.onboard_date < cfg.min_age_days * DAY_MS:
        return False, "çok yeni listelenmiş"
    if ticker is None:
        return False, "fiyat verisi yok"
    if ticker.quote_volume < cfg.min_quote_volume:
        return False, "hacim düşük"
    if ticker.last > 0:
        tick_ratio = float(Decimal(info.tick_size)) / ticker.last
        if tick_ratio > 0.0005:
            return False, "fiyat adımı kaba"
    return True, ""


def select_universe(
    symbols: dict[str, SymbolInfo],
    tickers: dict[str, Ticker],
    cfg: ScannerSettings,
    now: int,
) -> list[str]:
    ranked: list[tuple[float, str]] = []
    for sym, info in symbols.items():
        ok, _ = eligible(info, tickers.get(sym), cfg, now)
        if ok:
            ranked.append((tickers[sym].quote_volume, sym))
    ranked.sort(reverse=True)
    out = [s for _, s in ranked[: cfg.max_symbols]]
    for must in ALWAYS_INCLUDE:
        if must in symbols and must not in out and must not in cfg.blacklist:
            out.append(must)
    return out
