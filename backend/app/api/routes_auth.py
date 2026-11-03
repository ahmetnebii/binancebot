"""Arayüz parolası ve oturum yönetimi."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..paths import cookie_secure
from ..security import (
    MIN_PASSWORD_LEN,
    SESSION_COOKIE,
    SESSION_TTL_MS,
    hash_password,
    new_session_token,
    token_hash,
    verify_password,
)
from ..util import now_ms
from .deps import ctx_of, is_authenticated, require_csrf

router = APIRouter(prefix="/api/auth", dependencies=[Depends(require_csrf)])


class PasswordBody(BaseModel):
    password: str


async def create_session(request: Request, response: Response) -> None:
    ctx = ctx_of(request)
    token, th = new_session_token()
    now = now_ms()
    await ctx.db.session_purge(now)
    await ctx.db.session_add(th, now, now + SESSION_TTL_MS)
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_TTL_MS // 1000, httponly=True,
        samesite="strict", secure=cookie_secure(), path="/",
    )


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


@router.get("/state")
async def state(request: Request) -> dict:
    has = await ctx_of(request).store.password_hash() is not None
    return {"setup_required": not has, "authenticated": await is_authenticated(request) if has else False}


@router.post("/setup")
async def setup(body: PasswordBody, request: Request, response: Response) -> dict:
    ctx = ctx_of(request)
    if await ctx.store.password_hash() is not None:
        raise HTTPException(409, "Kurulum zaten tamamlanmış; giriş yapın")
    if len(body.password) < MIN_PASSWORD_LEN:
        raise HTTPException(400, f"Parola en az {MIN_PASSWORD_LEN} karakter olmalı")
    await ctx.store.set_password_hash(hash_password(body.password))
    await create_session(request, response)
    await ctx.events.log("info", "auth", "Arayüz parolası oluşturuldu")
    return {"ok": True}


@router.post("/login")
async def login(body: PasswordBody, request: Request, response: Response) -> dict:
    ctx = ctx_of(request)
    ip = client_ip(request)
    if ctx.limiter.blocked(ip):
        raise HTTPException(429, "Çok fazla hatalı deneme. 5 dakika sonra tekrar deneyin.")
    stored = await ctx.store.password_hash()
    if stored is None:
        raise HTTPException(409, "Önce kurulum yapılmalı")
    if not verify_password(body.password, stored):
        ctx.limiter.fail(ip)
        await ctx.events.log("warning", "auth", f"Hatalı giriş denemesi ({ip})")
        raise HTTPException(401, "Parola hatalı")
    ctx.limiter.reset(ip)
    await create_session(request, response)
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await ctx_of(request).db.session_delete(token_hash(token))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}
