"""Küçük yardımcılar: zaman, kimlik, sayısal işlemler."""
from __future__ import annotations

import math
import time
import uuid

TF_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

DAY_MS = 86_400_000


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def tf_ms(tf: str) -> int:
    try:
        return TF_MS[tf]
    except KeyError as exc:
        raise ValueError(f"Desteklenmeyen zaman dilimi: {tf}") from exc


def floor_time(ts_ms: int, tf: str) -> int:
    step = tf_ms(tf)
    return ts_ms - (ts_ms % step)


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def finite(x: float | None, default: float = 0.0) -> float:
    if x is None:
        return default
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return default
    return xf if math.isfinite(xf) else default


def utc_day_start(ts_ms: int) -> int:
    return ts_ms - (ts_ms % DAY_MS)


def utc_week_start(ts_ms: int) -> int:
    """Pazartesi 00:00 UTC (1970-01-01 bir Perşembe günüdür)."""
    day = utc_day_start(ts_ms)
    weekday = (day // DAY_MS + 3) % 7  # 0 = Pazartesi
    return day - weekday * DAY_MS
