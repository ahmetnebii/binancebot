"""Gizli değer şifreleme, parola özeti, oturum ve giriş denemesi sınırlaması."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

SESSION_COOKIE = "bb_session"
SESSION_TTL_MS = 7 * 24 * 3600 * 1000
MIN_PASSWORD_LEN = 10


class SecretBox:
    """API anahtarı ve token gibi değerleri diskte şifreli tutar (Fernet: AES-128-CBC + HMAC).

    Ana anahtar BB_MASTER_KEY ortam değişkeninden ya da veri klasöründeki
    `master.key` dosyasından (izin 600) okunur; yoksa ilk açılışta üretilir.
    """

    def __init__(self, data_dir: Path):
        env_key = os.environ.get("BB_MASTER_KEY", "").strip()
        if env_key:
            key = env_key.encode()
        else:
            kp = data_dir / "master.key"
            if kp.exists():
                key = kp.read_bytes().strip()
            else:
                key = Fernet.generate_key()
                fd = os.open(str(kp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(key)
        self._f = Fernet(key)

    def encrypt(self, value: str) -> bytes:
        return self._f.encrypt(value.encode("utf-8"))

    def decrypt(self, token: bytes) -> str | None:
        try:
            return self._f.decrypt(token).decode("utf-8")
        except InvalidToken:
            return None


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    n, r, p = 2**14, 8, 1
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
        )
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


def mask_hint(value: str) -> str:
    v = value.strip()
    if len(v) <= 4:
        return "••••"
    return "••••" + v[-4:]


def new_session_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class LoginLimiter:
    """IP başına başarısız giriş denemelerini sınırlar (5 deneme → 5 dakika kilit)."""

    def __init__(self, max_failures: int = 5, window_s: int = 300):
        self.max_failures = max_failures
        self.window_s = window_s
        self._failures: dict[str, list[float]] = {}

    def blocked(self, ip: str) -> bool:
        now = time.time()
        recent = [t for t in self._failures.get(ip, []) if now - t < self.window_s]
        self._failures[ip] = recent
        return len(recent) >= self.max_failures

    def fail(self, ip: str) -> None:
        self._failures.setdefault(ip, []).append(time.time())

    def reset(self, ip: str) -> None:
        self._failures.pop(ip, None)
