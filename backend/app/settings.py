"""Çalışma zamanı ayarları (arayüzden düzenlenir) ve şifreli gizli değerler."""
from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .db import Database
from .security import SecretBox, mask_hint
from .util import now_ms

log = logging.getLogger(__name__)

Env = Literal["paper", "testnet", "live"]


class ConnectionSettings(BaseModel):
    env: Env = "paper"
    key_type: Literal["hmac", "ed25519"] = "hmac"
    live_rest: str = "https://fapi.binance.com"
    live_ws: str = "wss://fstream.binance.com"
    testnet_rest: str = "https://demo-fapi.binance.com"
    testnet_ws: str = "wss://demo-fstream.binance.com"
    user_stream_path: str = "/private/ws?listenKey={listenKey}"
    market_stream_path: str = "/market/stream"
    public_stream_path: str = "/public/stream"

    @field_validator("live_rest", "testnet_rest")
    @classmethod
    def _https(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith("https://"):
            raise ValueError("REST adresi https:// ile başlamalı")
        return v

    @field_validator("live_ws", "testnet_ws")
    @classmethod
    def _wss(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith("wss://"):
            raise ValueError("WebSocket adresi wss:// ile başlamalı")
        return v

    @field_validator("user_stream_path")
    @classmethod
    def _user_path(cls, v: str) -> str:
        v = v.strip()
        if "{listenKey}" not in v or not v.startswith("/"):
            raise ValueError("Kullanıcı akışı yolu '/' ile başlamalı ve {listenKey} içermeli")
        return v

    @field_validator("market_stream_path", "public_stream_path")
    @classmethod
    def _path(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("/"):
            raise ValueError("Yol '/' ile başlamalı")
        return v

    def rest_base(self) -> str:
        return self.live_rest if self.env == "live" else self.testnet_rest

    def ws_base(self) -> str:
        return self.live_ws if self.env == "live" else self.testnet_ws


class TelegramSettings(BaseModel):
    enabled: bool = False
    chat_id: str = ""
    notify_signals: bool = True
    notify_trades: bool = True
    daily_report: bool = True

    @field_validator("chat_id")
    @classmethod
    def _chat(cls, v: str) -> str:
        v = str(v).strip()
        if v and not v.lstrip("-").isdigit():
            raise ValueError("Chat ID sayısal olmalı")
        return v


class RiskSettings(BaseModel):
    profile: Literal["temkinli", "dengeli", "agresif", "ozel"] = "dengeli"
    base_risk_pct: float = Field(0.0075, ge=0.001, le=0.02)
    min_risk_pct: float = Field(0.0025, ge=0.0005, le=0.02)
    max_risk_pct: float = Field(0.02, ge=0.001, le=0.03)
    liq_buffer_k: float = Field(3.0, ge=1.5, le=10.0)
    max_margin_per_trade_pct: float = Field(0.15, ge=0.01, le=0.5)
    cap_major: int = Field(20, ge=1, le=50)
    cap_large: int = Field(10, ge=1, le=50)
    cap_other: int = Field(5, ge=1, le=50)
    global_max_leverage: int = Field(20, ge=1, le=50)
    max_positions: int = Field(5, ge=1, le=20)
    max_same_side: int = Field(4, ge=1, le=20)
    capital_tiers: bool = True
    max_portfolio_heat_pct: float = Field(0.04, ge=0.005, le=0.2)
    max_total_margin_pct: float = Field(0.6, ge=0.05, le=0.95)
    max_effective_leverage: float = Field(3.0, ge=0.5, le=10.0)
    max_cluster_risk_pct: float = Field(0.025, ge=0.005, le=0.2)
    cluster_corr: float = Field(0.7, ge=0.3, le=0.99)
    daily_loss_pct: float = Field(0.03, ge=0.005, le=0.5)
    weekly_loss_pct: float = Field(0.06, ge=0.01, le=0.5)
    max_drawdown_pct: float = Field(0.2, ge=0.02, le=0.9)
    loss_streak_count: int = Field(5, ge=2, le=50)
    loss_streak_cooldown_min: int = Field(120, ge=0, le=10080)
    min_ev_r: float = Field(0.10, ge=-1.0, le=2.0)
    max_entry_drift_r: float = Field(0.3, ge=0.05, le=2.0)

    @model_validator(mode="after")
    def _order(self) -> "RiskSettings":
        if not (self.min_risk_pct <= self.base_risk_pct <= self.max_risk_pct):
            raise ValueError("Risk oranları min ≤ taban ≤ maks olmalı")
        return self


class ScannerSettings(BaseModel):
    min_quote_volume: float = Field(20_000_000, ge=0)
    min_age_days: int = Field(14, ge=0, le=365)
    max_symbols: int = Field(120, ge=5, le=400)
    max_spread_bps: float = Field(5.0, ge=0.5, le=100.0)
    blacklist: list[str] = Field(default_factory=list)
    signal_ttl_min: int = Field(20, ge=1, le=240)

    @field_validator("blacklist")
    @classmethod
    def _bl(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for s in v:
            s2 = str(s).strip().upper()
            if s2 and s2 not in out:
                out.append(s2)
        return out


class StrategySettings(BaseModel):
    s1_enabled: bool = True
    s2_enabled: bool = True
    s3_enabled: bool = False  # yatay piyasa stratejisi: backtest ile doğrulanmadan açılmaz
    s1_donchian: int = Field(48, ge=10, le=200)
    s1_vol_z: float = Field(2.0, ge=0.5, le=6.0)
    s1_atr_mult: float = Field(2.0, ge=0.5, le=6.0)
    s2_top_n: int = Field(3, ge=1, le=10)
    s3_bb_k: float = Field(2.5, ge=2.0, le=3.5)
    s3_rsi: float = Field(25.0, ge=15.0, le=35.0)
    oi_confirmation: bool = True
    health_monitor: bool = True
    health_min_trades: int = Field(20, ge=10, le=100)
    health_min_expectancy_r: float = Field(-0.15, ge=-1.0, le=0.0)
    s1_sides: Literal["both", "long", "short"] = "both"
    s2_sides: Literal["both", "long", "short"] = "both"
    s3_sides: Literal["both", "long", "short"] = "both"
    # S4: 4 saatlik trend takibi (zaman serisi momentumu, düşük işlem sıklığı)
    s4_enabled: bool = False
    s4_donchian: int = Field(30, ge=10, le=120)
    s4_atr_mult: float = Field(2.5, ge=1.0, le=6.0)
    s4_sides: Literal["both", "long", "short"] = "both"
    # Ortak filtreler (tüm stratejilere uygulanır)
    regime_filter: Literal["normal", "strict"] = "normal"
    funding_max: float = Field(0.0005, ge=0.0001, le=0.005)
    exit_profile: Literal["standard", "runner", "quick"] = "standard"


class PaperSettings(BaseModel):
    starting_balance: float = Field(1000.0, ge=10, le=10_000_000)
    slippage_bps: float = Field(3.0, ge=0, le=100)
    taker_fee: float = Field(0.0005, ge=0, le=0.01)
    maker_fee: float = Field(0.0002, ge=0, le=0.01)


class RuntimeState(BaseModel):
    auto_mode: bool = False
    paused: bool = False
    paused_reason: str = ""


PROFILES: dict[str, dict[str, Any]] = {
    "temkinli": {
        "base_risk_pct": 0.005, "max_positions": 3, "max_same_side": 2,
        "cap_major": 10, "cap_large": 5, "cap_other": 3, "global_max_leverage": 10,
        "max_portfolio_heat_pct": 0.025, "daily_loss_pct": 0.02, "weekly_loss_pct": 0.04,
        "max_drawdown_pct": 0.15,
    },
    "dengeli": {
        "base_risk_pct": 0.0075, "max_positions": 5, "max_same_side": 4,
        "cap_major": 20, "cap_large": 10, "cap_other": 5, "global_max_leverage": 20,
        "max_portfolio_heat_pct": 0.04, "daily_loss_pct": 0.03, "weekly_loss_pct": 0.06,
        "max_drawdown_pct": 0.20,
    },
    "agresif": {
        "base_risk_pct": 0.015, "max_positions": 6, "max_same_side": 5,
        "cap_major": 20, "cap_large": 10, "cap_other": 5, "global_max_leverage": 20,
        "max_portfolio_heat_pct": 0.06, "daily_loss_pct": 0.05, "weekly_loss_pct": 0.10,
        "max_drawdown_pct": 0.25,
    },
}

SECTIONS: dict[str, type[BaseModel]] = {
    "connection": ConnectionSettings,
    "telegram": TelegramSettings,
    "risk": RiskSettings,
    "scanner": ScannerSettings,
    "strategies": StrategySettings,
    "paper": PaperSettings,
    "runtime": RuntimeState,
}

SECRET_NAMES = ("binance_api_key", "binance_api_secret", "binance_private_key", "telegram_bot_token")


class SettingsStore:
    def __init__(self, db: Database, box: SecretBox):
        self.db = db
        self.box = box
        self.connection = ConnectionSettings()
        self.telegram = TelegramSettings()
        self.risk = RiskSettings()
        self.scanner = ScannerSettings()
        self.strategies = StrategySettings()
        self.paper = PaperSettings()
        self.runtime = RuntimeState()
        self._secrets: dict[str, Optional[str]] = {}

    async def load(self) -> None:
        for name, model in SECTIONS.items():
            raw = await self.db.kv_get(f"settings:{name}")
            if not raw:
                continue
            try:
                merged = {**model().model_dump(), **{k: v for k, v in raw.items() if k in model.model_fields}}
                setattr(self, name, model.model_validate(merged))
            except ValidationError as exc:
                log.warning("Ayar bölümü '%s' okunamadı, varsayılanlar kullanılıyor: %s", name, exc)

    async def save(self, name: str) -> None:
        await self.db.kv_set(f"settings:{name}", getattr(self, name).model_dump(), now_ms())

    async def update(self, name: str, patch: dict[str, Any]) -> BaseModel:
        model = SECTIONS[name]
        current = getattr(self, name)
        clean = {k: v for k, v in patch.items() if k in model.model_fields and v is not None}
        if name == "risk":
            prof = clean.get("profile")
            if prof in PROFILES:
                clean = {**clean, **PROFILES[prof]}
            elif any(k != "profile" for k in clean):
                clean["profile"] = "ozel"
        new = model.model_validate({**current.model_dump(), **clean})
        setattr(self, name, new)
        await self.save(name)
        return new

    # --- gizli değerler ------------------------------------------------
    async def set_secret(self, name: str, value: str) -> None:
        if name not in SECRET_NAMES:
            raise ValueError("Bilinmeyen gizli alan")
        v = value.strip()
        if not v:
            raise ValueError("Boş değer kaydedilemez")
        hint = "PEM anahtar" if name == "binance_private_key" else mask_hint(v)
        await self.db.secret_set(name, self.box.encrypt(v), hint, now_ms())
        self._secrets[name] = v

    async def clear_secret(self, name: str) -> None:
        if name not in SECRET_NAMES:
            raise ValueError("Bilinmeyen gizli alan")
        await self.db.secret_delete(name)
        self._secrets[name] = None

    async def get_secret(self, name: str) -> Optional[str]:
        if name in self._secrets:
            return self._secrets[name]
        row = await self.db.secret_get(name)
        value = self.box.decrypt(row[0]) if row else None
        if row and value is None:
            log.error("'%s' çözülemedi: ana anahtar (master.key) değişmiş olabilir", name)
        self._secrets[name] = value
        return value

    async def secret_meta(self, name: str) -> tuple[bool, str]:
        row = await self.db.secret_get(name)
        return (row is not None, row[1] if row else "")

    # --- arayüz parolası -----------------------------------------------
    async def password_hash(self) -> Optional[str]:
        return await self.db.kv_get("auth:password")

    async def set_password_hash(self, value: str) -> None:
        await self.db.kv_set("auth:password", value, now_ms())

    # --- arayüz görünümü -----------------------------------------------
    async def view(self) -> dict[str, Any]:
        key_set, key_hint = await self.secret_meta("binance_api_key")
        sec_set, _ = await self.secret_meta("binance_api_secret")
        pk_set, _ = await self.secret_meta("binance_private_key")
        tg_set, tg_hint = await self.secret_meta("telegram_bot_token")
        c = self.connection
        return {
            "connection": {
                "env": c.env,
                "key_type": c.key_type,
                "api_key_set": key_set,
                "api_key_hint": key_hint,
                "api_secret_set": sec_set,
                "private_key_set": pk_set,
                "live_rest": c.live_rest,
                "live_ws": c.live_ws,
                "testnet_rest": c.testnet_rest,
                "testnet_ws": c.testnet_ws,
                "user_stream_path": c.user_stream_path,
                "market_stream_path": c.market_stream_path,
            },
            "telegram": {
                **self.telegram.model_dump(),
                "bot_token_set": tg_set,
                "bot_token_hint": tg_hint,
            },
            "risk": self.risk.model_dump(),
            "scanner": self.scanner.model_dump(),
            "strategies": self.strategies.model_dump(),
            "paper": self.paper.model_dump(),
            "profiles": PROFILES,
        }
