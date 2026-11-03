"""Binance USDⓈ-M imzalı REST istemcisi (HMAC-SHA256 veya Ed25519)."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
from typing import Any, Optional
from urllib.parse import quote, urlencode

import aiohttp
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ..util import now_ms

log = logging.getLogger(__name__)

ERROR_TR: dict[int, str] = {
    -1003: "Çok fazla istek (hız limiti)",
    -1021: "Saat farkı: istek zaman penceresi dışında",
    -1022: "İmza geçersiz (secret veya özel anahtar hatalı)",
    -1111: "Hassasiyet hatası (fiyat/miktar ondalık sayısı)",
    -2014: "API anahtarı biçimi geçersiz",
    -2015: "API anahtarı, IP kısıtlaması veya yetki geçersiz",
    -2019: "Marjin yetersiz",
    -2021: "Emir anında tetiklenirdi (stop fiyatı yanlış tarafta)",
    -2022: "ReduceOnly emri reddedildi (pozisyon kapanmış olabilir)",
    -4014: "Fiyat, tick adımına uymuyor",
    -4023: "Miktar, step adımına uymuyor",
    -4046: "Marjin tipi zaten ayarlı",
    -4059: "Pozisyon modu zaten ayarlı",
    -4120: "Koşullu emir Algo API'ye gönderilmeli",
    -4164: "Pozisyon değeri borsanın minimumunun altında",
}


class BinanceAPIError(Exception):
    def __init__(self, status: int, code: Optional[int], msg: str, unknown: bool = False):
        super().__init__(f"[HTTP {status} / {code}] {msg}")
        self.status = status
        self.code = code
        self.msg = msg
        self.unknown = unknown  # emir durumu bilinmiyor (zaman aşımı / 5xx)

    def describe(self) -> str:
        if self.code is not None and self.code in ERROR_TR:
            return f"{ERROR_TR[self.code]} (kod {self.code})"
        return self.msg


def load_ed25519(pem: str) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem.strip().encode("utf-8"), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Özel anahtar Ed25519 türünde değil")
    return key


class BinanceREST:
    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        private_key_pem: Optional[str] = None,
        recv_window: int = 5000,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._secret = api_secret.encode("utf-8") if api_secret else None
        self._ed = load_ed25519(private_key_pem) if private_key_pem else None
        self.recv_window = recv_window
        self.time_offset = 0
        self.used_weight = 0
        self.order_count_1m = 0
        self.ban_until = 0
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def session(self) -> Optional[aiohttp.ClientSession]:
        return self._session

    async def start(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        await self.sync_time()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def sync_time(self) -> None:
        t0 = now_ms()
        data = await self.request("GET", "/fapi/v1/time")
        t1 = now_ms()
        self.time_offset = int(data["serverTime"]) - (t0 + t1) // 2

    def sign(self, payload: str) -> str:
        if self._ed is not None:
            return base64.b64encode(self._ed.sign(payload.encode("utf-8"))).decode("ascii")
        if self._secret is not None:
            return hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        raise BinanceAPIError(0, None, "İmzalama anahtarı tanımlı değil (API secret veya Ed25519 özel anahtar)")

    def build_query(self, params: Optional[dict[str, Any]], signed: bool) -> str:
        p: dict[str, Any] = {}
        for k, v in (params or {}).items():
            if v is None:
                continue
            if isinstance(v, bool):
                v = "true" if v else "false"
            p[k] = v
        if signed:
            p["timestamp"] = now_ms() + self.time_offset
            p["recvWindow"] = self.recv_window
        qs = urlencode(p)
        if signed:
            qs = f"{qs}&signature={quote(self.sign(qs), safe='')}"
        return qs

    async def request(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        signed: bool = False,
        key_only: bool = False,
    ) -> Any:
        if now_ms() < self.ban_until:
            raise BinanceAPIError(418, None, "IP geçici olarak kısıtlı; bekleniyor")
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        qs = self.build_query(params, signed)
        url = self.base_url + path + (f"?{qs}" if qs else "")
        headers: dict[str, str] = {}
        if signed or key_only:
            if not self.api_key:
                raise BinanceAPIError(0, None, "API anahtarı tanımlı değil")
            headers["X-MBX-APIKEY"] = self.api_key
        try:
            async with self._session.request(method, url, headers=headers) as r:
                w = r.headers.get("X-MBX-USED-WEIGHT-1M")
                if w and w.isdigit():
                    self.used_weight = int(w)
                oc = r.headers.get("X-MBX-ORDER-COUNT-1M")
                if oc and oc.isdigit():
                    self.order_count_1m = int(oc)
                text = await r.text()
                if r.status in (418, 429):
                    retry = int(r.headers.get("Retry-After", "60") or 60)
                    self.ban_until = now_ms() + retry * 1000
                    raise BinanceAPIError(r.status, None, f"Hız limiti aşıldı; {retry} sn bekleniyor")
                if r.status >= 500:
                    raise BinanceAPIError(r.status, None, f"Sunucu hatası, emir durumu bilinmiyor: {text[:160]}", unknown=True)
                data = json.loads(text) if text else None
                if r.status >= 400:
                    code = data.get("code") if isinstance(data, dict) else None
                    msg = data.get("msg", text) if isinstance(data, dict) else text
                    if code == -1021 and path != "/fapi/v1/time":
                        try:
                            await self.sync_time()
                        except BinanceAPIError:
                            pass
                    raise BinanceAPIError(r.status, code, str(msg))
                return data
        except asyncio.TimeoutError as exc:
            raise BinanceAPIError(0, None, "Zaman aşımı, emir durumu bilinmiyor", unknown=True) from exc
        except aiohttp.ClientError as exc:
            raise BinanceAPIError(0, None, f"Ağ hatası: {exc}", unknown=True) from exc
