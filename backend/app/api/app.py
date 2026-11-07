"""FastAPI uygulaması: API, WebSocket ve derlenmiş arayüzün servis edilmesi."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..paths import VERSION, frontend_dist
from .context import AppContext
from .deps import SafeJSONResponse
from .routes_auth import router as auth_router
from .routes_backtest import router as backtest_router
from .routes_core import router as core_router
from .routes_crowd import router as crowd_router
from .routes_settings import router as settings_router
from .ws import router as ws_router

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; connect-src 'self' ws: wss:; script-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'"
    ),
}


def create_app(ctx: AppContext | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        context = ctx or AppContext()
        app.state.ctx = context
        await context.start()
        try:
            yield
        finally:
            await context.stop()

    app = FastAPI(
        title="binancebot", version=VERSION, lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url=None, default_response_class=SafeJSONResponse,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    app.include_router(auth_router)
    app.include_router(core_router)
    app.include_router(settings_router)
    app.include_router(backtest_router)
    app.include_router(crowd_router)
    app.include_router(ws_router)

    dist = frontend_dist()
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/") or full_path == "ws":
            raise HTTPException(404, "Bulunamadı")
        index = dist / "index.html"
        if not index.exists():
            return JSONResponse({"detail": "Arayüz derlenmemiş: frontend klasöründe 'npm run build' çalıştırın."}, status_code=503)
        if full_path:
            candidate = (dist / full_path).resolve()
            if candidate.is_file() and _inside(candidate, dist):
                return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
        return True
    except ValueError:
        return False
