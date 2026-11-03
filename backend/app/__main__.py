"""Çalıştırma: `python -m app --host 127.0.0.1 --port 8000` (backend klasöründen)."""
from __future__ import annotations

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="binancebot sunucusu")
    parser.add_argument("--host", default=os.environ.get("BB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BB_PORT", "8000")))
    args = parser.parse_args()
    uvicorn.run(
        "app.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        log_level="info",
        proxy_headers=True,
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )


if __name__ == "__main__":
    main()
