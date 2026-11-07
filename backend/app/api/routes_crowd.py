"""Kalabalık verisi ve funding radarı uç noktaları."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..crowd.analysis import analyze
from ..util import now_ms
from .deps import ctx_of, require_auth, require_csrf

router = APIRouter(prefix="/api/crowd", dependencies=[Depends(require_auth), Depends(require_csrf)])


def collector(request: Request):
    c = getattr(ctx_of(request), "crowd", None)
    if c is None:
        raise HTTPException(503, "Kalabalık verisi toplayıcısı hazır değil")
    return c


@router.get("/status")
async def status(request: Request) -> dict:
    c = collector(request)
    cov = await ctx_of(request).db.crowd_coverage()
    return {**cov, "state": c.state, "last_run": c.last_run, "last_error": c.last_error,
            "days": ((cov["last"] - cov["first"]) / 86_400_000) if cov["first"] and cov["last"] else 0.0}


@router.get("/funding-radar")
async def funding_radar(request: Request) -> dict:
    return await collector(request).radar()


@router.post("/paper/reset")
async def paper_reset(request: Request) -> dict:
    await collector(request).reset_paper()
    return {"ok": True}


@router.post("/collect")
async def collect_now(request: Request) -> dict:
    c = collector(request)
    await c.collect()
    await c.carry_paper_update()
    return {"ok": True, "last_run": c.last_run}


@router.get("/analysis")
async def analysis(request: Request) -> dict:
    db = ctx_of(request).db
    since = now_ms() - 400 * 86_400_000
    import asyncio

    crowd_rows = await db.crowd_rows(since)
    funding_rows = await db.funding_rows(since)
    return await asyncio.to_thread(analyze, crowd_rows, funding_rows)
