"""Durum, kontrol, fırsatlar, pozisyonlar, manuel emir, piyasa ve geçmiş uç noktaları."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..engine.engine import Engine, EngineError
from ..util import now_ms
from .deps import ctx_of, require_auth, require_csrf

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth), Depends(require_csrf)])

INTERVALS = {"1m", "5m", "15m", "1h", "4h"}


def engine_of(request: Request) -> Engine:
    eng = ctx_of(request).engine
    if eng is None:
        raise HTTPException(503, "Motor henüz başlamadı")
    return eng


def fail(exc: Exception) -> HTTPException:
    return HTTPException(400, str(exc))


# --- durum ve kontrol ---------------------------------------------------------
class AutoBody(BaseModel):
    enabled: bool


class PauseBody(BaseModel):
    paused: bool
    reason: str = ""


@router.get("/status")
async def status(request: Request) -> dict:
    return await engine_of(request).status()


@router.post("/control/auto")
async def control_auto(body: AutoBody, request: Request) -> dict:
    eng = engine_of(request)
    if body.enabled and eng.broker is None:
        raise HTTPException(400, f"Otomatik mod açılamaz: {eng.broker_message or 'broker hazır değil'}")
    await eng.set_auto(body.enabled)
    return await eng.status()


@router.post("/control/pause")
async def control_pause(body: PauseBody, request: Request) -> dict:
    eng = engine_of(request)
    await eng.set_paused(body.paused, body.reason or ("Panelden durduruldu" if body.paused else ""))
    return await eng.status()


@router.post("/control/close-all")
async def control_close_all(request: Request) -> dict:
    return {"closed": await engine_of(request).close_all()}


# --- fırsatlar ----------------------------------------------------------------
class ExecuteBody(BaseModel):
    risk_pct: Optional[float] = Field(default=None, gt=0, le=0.05)
    leverage: Optional[int] = Field(default=None, ge=1, le=125)


@router.get("/signals")
async def signals(request: Request, scope: Literal["active", "recent"] = "active") -> list[dict]:
    eng = engine_of(request)
    items = list(eng.signals.values())
    if scope == "active":
        items = [s for s in items if s.status == "ACTIVE"]
        items.sort(key=lambda s: (s.score, s.created_at), reverse=True)
    else:
        items.sort(key=lambda s: s.created_at, reverse=True)
        items = items[:200]
    out = []
    for s in items:
        s.price = eng.market.price(s.symbol)
        out.append(s.model_dump(mode="json"))
    return out


@router.post("/signals/{signal_id}/preview")
async def preview_signal(signal_id: str, body: ExecuteBody, request: Request) -> dict:
    try:
        return await engine_of(request).preview_signal(signal_id, body.risk_pct, body.leverage)
    except EngineError as exc:
        raise fail(exc) from exc


@router.post("/signals/{signal_id}/execute")
async def execute_signal(signal_id: str, body: ExecuteBody, request: Request) -> dict:
    try:
        pos = await engine_of(request).execute_signal(signal_id, "manual", body.risk_pct, body.leverage)
    except EngineError as exc:
        raise fail(exc) from exc
    return pos.model_dump(mode="json")


@router.post("/signals/{signal_id}/dismiss")
async def dismiss_signal(signal_id: str, request: Request) -> dict:
    try:
        sig = await engine_of(request).dismiss_signal(signal_id)
    except EngineError as exc:
        raise fail(exc) from exc
    return sig.model_dump(mode="json")


# --- pozisyonlar --------------------------------------------------------------
class CloseBody(BaseModel):
    fraction: float = Field(default=1.0, gt=0, le=1)


class StopBody(BaseModel):
    price: float = Field(gt=0)


class TPItem(BaseModel):
    price: float = Field(gt=0)
    fraction: float = Field(gt=0, le=1)


class TPBody(BaseModel):
    take_profits: list[TPItem]


@router.get("/positions")
async def positions(
    request: Request, status: Literal["open", "closed"] = "open", limit: int = Query(100, ge=1, le=1000)
) -> list[dict]:
    eng = engine_of(request)
    if status == "open":
        items = sorted(eng.positions.values(), key=lambda p: p.opened_at, reverse=True)
    else:
        items = await ctx_of(request).db.list_positions(eng.env, "CLOSED", limit)
    return [p.model_dump(mode="json") for p in items]


@router.get("/positions/{pos_id}")
async def position_detail(pos_id: str, request: Request) -> dict:
    detail = await engine_of(request).position_detail(pos_id)
    if detail is None:
        raise HTTPException(404, "Pozisyon bulunamadı")
    return detail


@router.post("/positions/{pos_id}/close")
async def position_close(pos_id: str, body: CloseBody, request: Request) -> dict:
    try:
        pos = await engine_of(request).close_position(pos_id, body.fraction, "Manuel kapanış" if body.fraction >= 0.999 else "Manuel kısmi kapanış")
    except EngineError as exc:
        raise fail(exc) from exc
    return pos.model_dump(mode="json")


@router.post("/positions/{pos_id}/stop")
async def position_stop(pos_id: str, body: StopBody, request: Request) -> dict:
    try:
        pos = await engine_of(request).move_stop(pos_id, body.price)
    except EngineError as exc:
        raise fail(exc) from exc
    return pos.model_dump(mode="json")


@router.post("/positions/{pos_id}/breakeven")
async def position_breakeven(pos_id: str, request: Request) -> dict:
    try:
        pos = await engine_of(request).breakeven(pos_id)
    except EngineError as exc:
        raise fail(exc) from exc
    return pos.model_dump(mode="json")


@router.post("/positions/{pos_id}/take-profits")
async def position_tps(pos_id: str, body: TPBody, request: Request) -> dict:
    try:
        pos = await engine_of(request).set_take_profits(pos_id, [t.model_dump() for t in body.take_profits])
    except EngineError as exc:
        raise fail(exc) from exc
    return pos.model_dump(mode="json")


# --- manuel emir --------------------------------------------------------------
class ManualTP(BaseModel):
    r: Optional[float] = Field(default=None, gt=0)
    price: Optional[float] = Field(default=None, gt=0)
    fraction: float = Field(gt=0, le=1)


class ManualOrder(BaseModel):
    symbol: str
    side: Literal["LONG", "SHORT"]
    stop_price: float = Field(gt=0)
    take_profits: Optional[list[ManualTP]] = None
    risk_pct: Optional[float] = Field(default=None, gt=0, le=0.05)
    leverage: Optional[int] = Field(default=None, ge=1, le=125)


@router.post("/orders/preview")
async def order_preview(body: ManualOrder, request: Request) -> dict:
    tps = [t.model_dump() for t in body.take_profits] if body.take_profits else None
    try:
        return await engine_of(request).preview_manual(body.symbol.upper(), body.side, body.stop_price, tps, body.risk_pct, body.leverage)
    except EngineError as exc:
        raise fail(exc) from exc


@router.post("/orders/manual")
async def order_manual(body: ManualOrder, request: Request) -> dict:
    tps = [t.model_dump() for t in body.take_profits] if body.take_profits else None
    try:
        pos = await engine_of(request).manual_order(body.symbol.upper(), body.side, body.stop_price, tps, body.risk_pct, body.leverage)
    except EngineError as exc:
        raise fail(exc) from exc
    return pos.model_dump(mode="json")


# --- piyasa -------------------------------------------------------------------
@router.get("/market/symbols")
async def market_symbols(request: Request) -> list[dict]:
    m = engine_of(request).market
    uni = set(m.universe)
    out = []
    for sym in m.symbols:
        t = m.tickers.get(sym)
        if t is None:
            continue
        fr = m.funding(sym)
        out.append({
            "symbol": sym,
            "price": t.last,
            "change_pct": t.change_pct,
            "quote_volume": t.quote_volume,
            "funding_rate": fr.funding_rate if fr else None,
            "in_universe": sym in uni,
        })
    out.sort(key=lambda x: x["quote_volume"], reverse=True)
    return out[:800]


@router.get("/market/klines")
async def market_klines(
    request: Request, symbol: str, interval: str = "15m", limit: int = Query(300, ge=10, le=1000)
) -> list[dict]:
    if interval not in INTERVALS:
        raise HTTPException(400, "Desteklenmeyen zaman dilimi")
    m = engine_of(request).market
    symbol = symbol.upper()
    if symbol not in m.symbols:
        raise HTTPException(404, "Sembol bulunamadı")
    try:
        candles = await m.klines(symbol, interval, limit)
    except Exception as exc:
        raise HTTPException(502, f"Mum verisi alınamadı: {exc}") from exc
    return [c.to_chart() for c in candles]


# --- geçmiş -------------------------------------------------------------------
RANGES = {"1d": 86_400_000, "7d": 7 * 86_400_000, "30d": 30 * 86_400_000, "all": 10 * 365 * 86_400_000}


@router.get("/history/equity")
async def history_equity(request: Request, range: Literal["1d", "7d", "30d", "all"] = "7d") -> list[dict]:  # noqa: A002
    eng = engine_of(request)
    rows = await ctx_of(request).db.list_equity(eng.env, now_ms() - RANGES[range])
    out = [{"time": ts // 1000, "equity": eq} for ts, eq in rows]
    view = eng._equity_view()
    if view:
        t = now_ms() // 1000
        if not out or t > out[-1]["time"]:
            out.append({"time": t, "equity": view["equity"]})
    return out


@router.get("/history/stats")
async def history_stats(request: Request) -> dict:
    eng = engine_of(request)
    closed = [p for p in await ctx_of(request).db.list_positions(eng.env, "CLOSED", 1000) if p.close_reason != "Simülasyon sıfırlandı"]
    summary = eng.stats_summary(closed)
    if summary["profit_factor"] == float("inf"):
        summary["profit_factor"] = None
    rs = [p.r_final for p in closed if p.r_final is not None]
    wins_r = [r for r in rs if r > 0]
    loss_r = [r for r in rs if r <= 0]
    by: dict[str, dict] = {}
    for p in closed:
        d = by.setdefault(p.strategy, {"strategy": p.strategy, "strategy_name": p.strategy_name, "trades": 0, "wins": 0, "total_pnl": 0.0, "sum_r": 0.0, "n_r": 0})
        net = p.realized_pnl - p.fees + p.funding
        d["trades"] += 1
        d["wins"] += int(net > 0)
        d["total_pnl"] += net
        if p.r_final is not None:
            d["sum_r"] += p.r_final
            d["n_r"] += 1
    by_strategy = [
        {
            "strategy": d["strategy"], "strategy_name": d["strategy_name"], "trades": d["trades"],
            "win_rate": d["wins"] / d["trades"] if d["trades"] else None, "total_pnl": d["total_pnl"],
            "expectancy_r": d["sum_r"] / d["n_r"] if d["n_r"] else None,
        }
        for d in by.values()
    ]
    return {
        **summary,
        "wins": sum(1 for p in closed if p.realized_pnl - p.fees + p.funding > 0),
        "losses": sum(1 for p in closed if p.realized_pnl - p.fees + p.funding <= 0),
        "avg_win_r": sum(wins_r) / len(wins_r) if wins_r else None,
        "avg_loss_r": sum(loss_r) / len(loss_r) if loss_r else None,
        "best_r": max(rs) if rs else None,
        "worst_r": min(rs) if rs else None,
        "by_strategy": by_strategy,
    }


@router.get("/events")
async def events(request: Request, limit: int = Query(200, ge=1, le=2000), level: Optional[str] = None) -> list[dict]:
    return await ctx_of(request).db.list_events(limit, level)
