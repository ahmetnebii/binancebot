"""Geriye dönük test (backtest) uç noktaları."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..backtest.manager import BacktestBusy
from .deps import ctx_of, require_auth, require_csrf

router = APIRouter(prefix="/api/backtest", dependencies=[Depends(require_auth), Depends(require_csrf)])


class StrategyFlags(BaseModel):
    s1: Optional[bool] = None
    s2: Optional[bool] = None
    s3: Optional[bool] = None
    s4: Optional[bool] = None
    s5: Optional[bool] = None


class BacktestConfigIn(BaseModel):
    days: Optional[int] = Field(default=None, ge=7, le=365)
    top_n: Optional[int] = Field(default=None, ge=5, le=80)
    symbols: Optional[list[str]] = None
    strategies: Optional[StrategyFlags] = None
    initial_equity: Optional[float] = Field(default=None, ge=10, le=100_000_000)
    slippage_bps: Optional[float] = Field(default=None, ge=0, le=100)
    taker_fee: Optional[float] = Field(default=None, ge=0, le=0.01)
    maker_fee: Optional[float] = Field(default=None, ge=0, le=0.01)


def manager(request: Request):
    m = ctx_of(request).backtests
    if m is None:
        raise HTTPException(503, "Backtest yöneticisi hazır değil")
    return m


@router.post("/run")
async def run(body: BacktestConfigIn, request: Request) -> dict:
    payload = body.model_dump(exclude_none=True)
    if body.strategies is not None:
        payload["strategies"] = body.strategies.model_dump(exclude_none=True)
    try:
        return await manager(request).run(payload)
    except BacktestBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/jobs")
async def jobs(request: Request) -> list[dict]:
    return await manager(request).list("backtest")


class OptimizeIn(BaseModel):
    days: Optional[int] = Field(default=None, ge=60, le=365)
    top_n: Optional[int] = Field(default=None, ge=5, le=80)
    budget: Optional[str] = None


@router.post("/optimize")
async def optimize_run(body: OptimizeIn, request: Request) -> dict:
    try:
        return await manager(request).run_optimize(body.model_dump(exclude_none=True))
    except BacktestBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/optimize/jobs")
async def optimize_jobs(request: Request) -> list[dict]:
    return await manager(request).list("optimize")


@router.post("/optimize/{job_id}/apply")
async def optimize_apply(job_id: str, request: Request) -> dict:
    try:
        return await manager(request).apply_optimization(job_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class LabIn(BaseModel):
    days: Optional[int] = Field(default=None, ge=90, le=1095)
    top_n: Optional[int] = Field(default=None, ge=3, le=60)
    budget: Optional[str] = None
    grouped: Optional[bool] = None
    mode: Optional[str] = Field(default=None, pattern="^(single|groups|coins)$")


class LabApplyIn(BaseModel):
    mode: str = Field(default="live", pattern="^(live|paper)$")


@router.post("/lab")
async def lab_run(body: LabIn, request: Request) -> dict:
    try:
        return await manager(request).run_lab_job(body.model_dump(exclude_none=True))
    except BacktestBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/lab/jobs")
async def lab_jobs(request: Request) -> list[dict]:
    return await manager(request).list("lab")


@router.post("/lab/{job_id}/apply")
async def lab_apply(job_id: str, request: Request, body: Optional[LabApplyIn] = None) -> dict:
    try:
        return await manager(request).apply_lab(job_id, (body or LabApplyIn()).mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class DataSyncIn(BaseModel):
    days: Optional[int] = Field(default=None, ge=30, le=1095)
    top_n: Optional[int] = Field(default=None, ge=3, le=60)


@router.get("/data/status")
async def data_status(request: Request) -> dict:
    return manager(request).data_status()


@router.post("/data/sync")
async def data_sync(body: DataSyncIn, request: Request) -> dict:
    try:
        return await manager(request).run_data_sync(body.model_dump(exclude_none=True))
    except BacktestBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class CarryIn(BaseModel):
    days: Optional[int] = Field(default=None, ge=90, le=1095)
    top_n: Optional[int] = Field(default=None, ge=5, le=1000)
    spot_fee: Optional[float] = Field(default=None, ge=0, le=0.01)
    leverage: Optional[float] = Field(default=None, ge=1, le=10)
    universe: Optional[str] = Field(default=None, pattern="^(binance|all|cross)$")


@router.post("/full")
async def full_run(request: Request) -> dict:
    try:
        return await manager(request).run_full({})
    except BacktestBusy as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/full/{job_id}/trades/{idx}")
async def full_trades(job_id: str, idx: int, request: Request) -> dict:
    try:
        return await manager(request).full_candidate_trades(job_id, idx)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/full/jobs")
async def full_jobs(request: Request) -> list[dict]:
    return await manager(request).list("full")


@router.post("/carry")
async def carry_run(body: CarryIn, request: Request) -> dict:
    try:
        return await manager(request).run_carry(body.model_dump(exclude_none=True))
    except BacktestBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/carry/jobs")
async def carry_jobs(request: Request) -> list[dict]:
    return await manager(request).list("carry")


@router.post("/carry/{job_id}/apply")
async def carry_apply(job_id: str, request: Request) -> dict:
    try:
        return await manager(request).apply_carry(job_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/jobs/{job_id}")
async def job(job_id: str, request: Request) -> dict:
    j = await manager(request).get(job_id)
    if j is None:
        raise HTTPException(404, "Backtest bulunamadı")
    return j


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: str, request: Request) -> dict:
    j = await manager(request).cancel(job_id)
    if j is None:
        raise HTTPException(404, "Backtest bulunamadı")
    return j


@router.delete("/jobs/{job_id}")
async def delete(job_id: str, request: Request) -> dict:
    try:
        await manager(request).delete(job_id)
    except BacktestBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@router.post("/jobs/{job_id}/apply-calibration")
async def apply_calibration(job_id: str, request: Request) -> dict:
    try:
        return await manager(request).apply_calibration(job_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/calibration")
async def get_calibration(request: Request) -> dict:
    return await manager(request).get_calibration()


@router.delete("/calibration")
async def reset_calibration(request: Request) -> dict:
    return await manager(request).reset_calibration()
