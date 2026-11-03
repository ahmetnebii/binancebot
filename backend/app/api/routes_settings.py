"""Ayarlar: bağlantı ve anahtarlar (şifreli), Telegram, risk, tarama, stratejiler, simülasyon."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ValidationError

from ..exchange.binance_client import BinanceAPIError, BinanceREST, load_ed25519
from ..security import MIN_PASSWORD_LEN, hash_password, verify_password
from .deps import ctx_of, require_auth, require_csrf
from .routes_auth import create_session

router = APIRouter(prefix="/api/settings", dependencies=[Depends(require_auth), Depends(require_csrf)])

SECRET_FIELDS = {"api_key": "binance_api_key", "api_secret": "binance_api_secret", "private_key": "binance_private_key"}


def validation_error(exc: ValidationError) -> HTTPException:
    msgs = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", []) if x != "__root__")
        msg = str(e.get("msg", "")).replace("Value error, ", "")
        msgs.append(f"{loc}: {msg}" if loc else msg)
    return HTTPException(422, "; ".join(msgs) or "Geçersiz değer")


async def _update(request: Request, section: str, patch: dict) -> dict:
    ctx = ctx_of(request)
    try:
        await ctx.store.update(section, patch)
    except ValidationError as exc:
        raise validation_error(exc) from exc
    return await ctx.store.view()


@router.get("")
async def get_settings(request: Request) -> dict:
    return await ctx_of(request).store.view()


class ConnectionPatch(BaseModel):
    env: Optional[Literal["paper", "testnet", "live"]] = None
    key_type: Optional[Literal["hmac", "ed25519"]] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    private_key: Optional[str] = None
    clear: list[str] = []
    live_rest: Optional[str] = None
    live_ws: Optional[str] = None
    testnet_rest: Optional[str] = None
    testnet_ws: Optional[str] = None
    user_stream_path: Optional[str] = None
    market_stream_path: Optional[str] = None
    confirm_live: Optional[str] = None


@router.put("/connection")
async def put_connection(body: ConnectionPatch, request: Request) -> dict:
    ctx = ctx_of(request)
    store = ctx.store
    old = store.connection.model_copy()
    if body.env and body.env != "paper" and ctx.synthetic:
        raise HTTPException(400, "Sentetik geliştirme modunda yalnızca simülasyon ortamı kullanılabilir")
    if body.env == "live" and old.env != "live" and (body.confirm_live or "").strip().upper() != "CANLI":
        raise HTTPException(400, "Canlı ortama geçmek için onay metni olarak CANLI yazılmalı")
    if body.private_key:
        try:
            load_ed25519(body.private_key)
        except Exception as exc:
            raise HTTPException(400, f"Ed25519 özel anahtarı okunamadı: {exc}") from exc
    secrets_changed = False
    for field, name in SECRET_FIELDS.items():
        value = getattr(body, field)
        if value is not None and value.strip():
            await store.set_secret(name, value)
            secrets_changed = True
    for field in body.clear:
        name = SECRET_FIELDS.get(field)
        if name:
            await store.clear_secret(name)
            secrets_changed = True
    patch = body.model_dump(exclude={"api_key", "api_secret", "private_key", "clear", "confirm_live"}, exclude_none=True)
    try:
        new = await store.update("connection", patch)
    except ValidationError as exc:
        raise validation_error(exc) from exc
    env_changed = new.env != old.env
    urls_changed = any(
        getattr(new, f) != getattr(old, f)
        for f in ("live_rest", "live_ws", "testnet_rest", "testnet_ws", "user_stream_path", "market_stream_path", "key_type")
    )
    if secrets_changed:
        await ctx.events.log("info", "settings", "API anahtarları güncellendi (şifreli olarak saklandı)")
    if ctx.engine and (env_changed or ((secrets_changed or urls_changed) and new.env != "paper")):
        label = {"paper": "Simülasyon", "testnet": "Testnet", "live": "CANLI"}[new.env]
        await ctx.engine.reload(f"ortam: {label}" if env_changed else "bağlantı ayarları değişti")
    return await store.view()


@router.post("/connection/test")
async def test_connection(request: Request) -> dict:
    ctx = ctx_of(request)
    store = ctx.store
    conn = store.connection
    env = "paper" if ctx.synthetic else conn.env
    if env == "paper":
        eq = None
        if ctx.engine and ctx.engine.broker:
            eq = (await ctx.engine.broker.account()).equity
        return {"ok": True, "message": "Simülasyon ortamı: API anahtarı gerekmez, gerçek emir gönderilmez.", "equity": eq}
    key = await store.get_secret("binance_api_key")
    secret = await store.get_secret("binance_api_secret")
    pem = await store.get_secret("binance_private_key")
    if not key:
        return {"ok": False, "message": "API anahtarı kayıtlı değil"}
    try:
        rest = BinanceREST(
            conn.rest_base(), api_key=key,
            api_secret=secret if conn.key_type == "hmac" else None,
            private_key_pem=pem if conn.key_type == "ed25519" else None,
        )
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    try:
        try:
            await rest.start()
        except BinanceAPIError as exc:
            return {"ok": False, "message": f"Binance'e ulaşılamadı ({conn.rest_base()}): {exc.describe()}"}
        try:
            d = await rest.request("GET", "/fapi/v3/account", signed=True)
        except BinanceAPIError as exc:
            return {"ok": False, "message": f"Anahtar doğrulanamadı: {exc.describe()}"}
        equity = float(d.get("totalWalletBalance", 0.0)) + float(d.get("totalUnrealizedProfit", 0.0))
        return {"ok": True, "message": f"Bağlantı başarılı. Saat farkı {rest.time_offset} ms.", "equity": equity}
    finally:
        await rest.close()


class TelegramPatch(BaseModel):
    enabled: Optional[bool] = None
    chat_id: Optional[str] = None
    bot_token: Optional[str] = None
    clear_token: bool = False
    notify_signals: Optional[bool] = None
    notify_trades: Optional[bool] = None


@router.put("/telegram")
async def put_telegram(body: TelegramPatch, request: Request) -> dict:
    store = ctx_of(request).store
    if body.bot_token and body.bot_token.strip():
        token = body.bot_token.strip()
        if ":" not in token:
            raise HTTPException(400, "Bot token biçimi geçersiz (ör. 123456:ABC...)")
        await store.set_secret("telegram_bot_token", token)
    if body.clear_token:
        await store.clear_secret("telegram_bot_token")
    patch = body.model_dump(exclude={"bot_token", "clear_token"}, exclude_none=True)
    return await _update(request, "telegram", patch)


@router.post("/telegram/test")
async def test_telegram(request: Request) -> dict:
    ok, msg = await ctx_of(request).notifier.send("✅ <b>binancebot</b> test mesajı: bildirimler çalışıyor.")
    return {"ok": ok, "message": msg}


@router.get("/telegram/chats")
async def telegram_chats(request: Request) -> list[dict]:
    try:
        return await ctx_of(request).notifier.discover_chats()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Telegram'a ulaşılamadı: {exc.__class__.__name__}") from exc


@router.put("/risk")
async def put_risk(body: dict, request: Request) -> dict:
    return await _update(request, "risk", body)


@router.put("/scanner")
async def put_scanner(body: dict, request: Request) -> dict:
    return await _update(request, "scanner", body)


@router.put("/strategies")
async def put_strategies(body: dict, request: Request) -> dict:
    return await _update(request, "strategies", body)


class PaperPatch(BaseModel):
    starting_balance: Optional[float] = None
    slippage_bps: Optional[float] = None
    taker_fee: Optional[float] = None
    maker_fee: Optional[float] = None
    reset: bool = False


@router.put("/paper")
async def put_paper(body: PaperPatch, request: Request) -> dict:
    ctx = ctx_of(request)
    view = await _update(request, "paper", body.model_dump(exclude={"reset"}, exclude_none=True))
    if body.reset:
        if ctx.engine is None:
            raise HTTPException(503, "Motor hazır değil")
        try:
            await ctx.engine.reset_paper(ctx.store.paper.starting_balance)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
    return view


class PasswordChange(BaseModel):
    current: str
    new: str


@router.post("/password")
async def change_password(body: PasswordChange, request: Request, response: Response) -> dict:
    ctx = ctx_of(request)
    stored = await ctx.store.password_hash()
    if not stored or not verify_password(body.current, stored):
        raise HTTPException(400, "Mevcut parola hatalı")
    if len(body.new) < MIN_PASSWORD_LEN:
        raise HTTPException(400, f"Yeni parola en az {MIN_PASSWORD_LEN} karakter olmalı")
    await ctx.store.set_password_hash(hash_password(body.new))
    await ctx.db.session_delete_all()
    await create_session(request, response)
    await ctx.events.log("info", "auth", "Arayüz parolası değiştirildi; diğer oturumlar kapatıldı")
    return {"ok": True}
