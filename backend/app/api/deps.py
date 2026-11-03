"""Ortak bağımlılıklar: kimlik doğrulama, CSRF, güvenli JSON yanıtı."""
from __future__ import annotations

import json
import math
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from ..security import SESSION_COOKIE, token_hash
from ..util import now_ms


def clean(obj: Any) -> Any:
    """NaN/sonsuz değerleri null'a çevirir (tarayıcı JSON'u bunları kabul etmez)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def dumps(obj: Any) -> str:
    return json.dumps(clean(obj), ensure_ascii=False, allow_nan=False, default=str)


class SafeJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return dumps(content).encode("utf-8")


def ctx_of(request: Request):
    return request.app.state.ctx


async def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    return await ctx_of(request).db.session_valid(token_hash(token), now_ms())


async def require_auth(request: Request) -> None:
    if not await is_authenticated(request):
        raise HTTPException(status_code=401, detail="Oturum gerekli")


async def require_csrf(request: Request) -> None:
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-bb-client") != "ui":
        raise HTTPException(status_code=403, detail="Geçersiz istek (CSRF koruması)")
