"""Dosya yolları ve ortam değişkenleri."""
from __future__ import annotations

import os
from pathlib import Path

VERSION = "0.1.0"

REPO_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Veritabanı, ana anahtar ve logların tutulduğu klasör (BB_DATA_DIR)."""
    d = Path(os.environ.get("BB_DATA_DIR", str(REPO_ROOT / "data")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def frontend_dist() -> Path:
    return Path(os.environ.get("BB_FRONTEND_DIST", str(REPO_ROOT / "frontend" / "dist")))


def data_source() -> str:
    """'binance' (varsayılan) veya 'synthetic'.

    'synthetic' YALNIZCA geliştirme/test içindir: gerçek olmayan piyasa verisi üretir
    ve sistemi zorunlu olarak simülasyon (paper) ortamında tutar.
    """
    src = os.environ.get("BB_DATA_SOURCE", "binance").strip().lower()
    return "synthetic" if src == "synthetic" else "binance"


def cookie_secure() -> bool:
    """HTTPS arkasında çalışırken çerezlere Secure bayrağı eklenir (BB_COOKIE_SECURE=1)."""
    return os.environ.get("BB_COOKIE_SECURE", "0") == "1"
