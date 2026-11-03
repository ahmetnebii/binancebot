"""SQLite kalıcılık katmanı (aiosqlite)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from .models import Position, Signal

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS kv (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER) * 1000)
    )""",
    """CREATE TABLE IF NOT EXISTS secrets (
        name TEXT PRIMARY KEY,
        ciphertext BLOB NOT NULL,
        hint TEXT NOT NULL DEFAULT '',
        updated_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS signals (
        id TEXT PRIMARY KEY,
        created_at INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        strategy TEXT NOT NULL,
        status TEXT NOT NULL,
        data TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status)",
    """CREATE TABLE IF NOT EXISTS positions (
        id TEXT PRIMARY KEY,
        env TEXT NOT NULL,
        symbol TEXT NOT NULL,
        status TEXT NOT NULL,
        opened_at INTEGER NOT NULL,
        closed_at INTEGER,
        data TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_positions_env_status ON positions(env, status)",
    """CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        level TEXT NOT NULL,
        kind TEXT NOT NULL,
        message TEXT NOT NULL,
        data TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)",
    """CREATE TABLE IF NOT EXISTS equity (
        env TEXT NOT NULL,
        ts INTEGER NOT NULL,
        equity REAL NOT NULL,
        wallet REAL NOT NULL,
        unrealized REAL NOT NULL,
        PRIMARY KEY (env, ts)
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL
    )""",
]


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.conn: Optional[aiosqlite.Connection] = None

    async def open(self) -> None:
        self.conn = await aiosqlite.connect(str(self.path))
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        for stmt in SCHEMA:
            await self.conn.execute(stmt)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    @property
    def c(self) -> aiosqlite.Connection:
        assert self.conn is not None, "Veritabanı açık değil"
        return self.conn

    # --- anahtar/değer -------------------------------------------------
    async def kv_get(self, key: str, default: Any = None) -> Any:
        async with self.c.execute("SELECT value FROM kv WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
        return json.loads(row[0]) if row else default

    async def kv_set(self, key: str, value: Any, ts: int = 0) -> None:
        await self.c.execute(
            "INSERT INTO kv(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), ts),
        )
        await self.c.commit()

    async def kv_delete(self, key: str) -> None:
        await self.c.execute("DELETE FROM kv WHERE key=?", (key,))
        await self.c.commit()

    # --- gizli değerler (şifreli) ---------------------------------------
    async def secret_get(self, name: str) -> Optional[tuple[bytes, str]]:
        async with self.c.execute("SELECT ciphertext, hint FROM secrets WHERE name=?", (name,)) as cur:
            row = await cur.fetchone()
        return (bytes(row[0]), row[1]) if row else None

    async def secret_set(self, name: str, ciphertext: bytes, hint: str, ts: int) -> None:
        await self.c.execute(
            "INSERT INTO secrets(name, ciphertext, hint, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET ciphertext=excluded.ciphertext, hint=excluded.hint, "
            "updated_at=excluded.updated_at",
            (name, ciphertext, hint, ts),
        )
        await self.c.commit()

    async def secret_delete(self, name: str) -> None:
        await self.c.execute("DELETE FROM secrets WHERE name=?", (name,))
        await self.c.commit()

    # --- sinyaller -----------------------------------------------------
    async def save_signal(self, s: Signal) -> None:
        await self.c.execute(
            "INSERT INTO signals(id, created_at, symbol, strategy, status, data) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, data=excluded.data",
            (s.id, s.created_at, s.symbol, s.strategy, s.status, s.model_dump_json()),
        )
        await self.c.commit()

    async def get_signal(self, sid: str) -> Optional[Signal]:
        async with self.c.execute("SELECT data FROM signals WHERE id=?", (sid,)) as cur:
            row = await cur.fetchone()
        return Signal.model_validate_json(row[0]) if row else None

    async def list_signals(self, since_ms: int = 0, statuses: Optional[list[str]] = None, limit: int = 300) -> list[Signal]:
        q = "SELECT data FROM signals WHERE created_at >= ?"
        args: list[Any] = [since_ms]
        if statuses:
            q += f" AND status IN ({','.join('?' * len(statuses))})"
            args.extend(statuses)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        async with self.c.execute(q, args) as cur:
            rows = await cur.fetchall()
        return [Signal.model_validate_json(r[0]) for r in rows]

    # --- pozisyonlar ---------------------------------------------------
    async def save_position(self, p: Position) -> None:
        await self.c.execute(
            "INSERT INTO positions(id, env, symbol, status, opened_at, closed_at, data) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, closed_at=excluded.closed_at, data=excluded.data",
            (p.id, p.env, p.symbol, p.status, p.opened_at, p.closed_at, p.model_dump_json()),
        )
        await self.c.commit()

    async def get_position(self, pid: str) -> Optional[Position]:
        async with self.c.execute("SELECT data FROM positions WHERE id=?", (pid,)) as cur:
            row = await cur.fetchone()
        return Position.model_validate_json(row[0]) if row else None

    async def list_positions(self, env: str, status: Optional[str] = None, limit: int = 200) -> list[Position]:
        q = "SELECT data FROM positions WHERE env=?"
        args: list[Any] = [env]
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY COALESCE(closed_at, opened_at) DESC LIMIT ?"
        args.append(limit)
        async with self.c.execute(q, args) as cur:
            rows = await cur.fetchall()
        return [Position.model_validate_json(r[0]) for r in rows]

    # --- olaylar -------------------------------------------------------
    async def add_event(self, ts: int, level: str, kind: str, message: str, data: Any = None) -> int:
        cur = await self.c.execute(
            "INSERT INTO events(ts, level, kind, message, data) VALUES(?,?,?,?,?)",
            (ts, level, kind, message, json.dumps(data) if data is not None else None),
        )
        await self.c.commit()
        return int(cur.lastrowid or 0)

    async def list_events(self, limit: int = 200, level: Optional[str] = None) -> list[dict]:
        q = "SELECT id, ts, level, kind, message FROM events"
        args: list[Any] = []
        if level:
            q += " WHERE level=?"
            args.append(level)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        async with self.c.execute(q, args) as cur:
            rows = await cur.fetchall()
        return [{"id": r[0], "ts": r[1], "level": r[2], "kind": r[3], "message": r[4]} for r in rows]

    async def prune_events(self, keep: int = 20000) -> None:
        await self.c.execute(
            "DELETE FROM events WHERE id <= (SELECT COALESCE(MAX(id),0) - ? FROM events)", (keep,)
        )
        await self.c.commit()

    # --- özsermaye geçmişi ---------------------------------------------
    async def add_equity(self, env: str, ts: int, equity: float, wallet: float, unrealized: float) -> None:
        await self.c.execute(
            "INSERT OR REPLACE INTO equity(env, ts, equity, wallet, unrealized) VALUES(?,?,?,?,?)",
            (env, ts, equity, wallet, unrealized),
        )
        await self.c.commit()

    async def list_equity(self, env: str, since_ms: int, max_points: int = 1500) -> list[tuple[int, float]]:
        async with self.c.execute(
            "SELECT ts, equity FROM equity WHERE env=? AND ts>=? ORDER BY ts", (env, since_ms)
        ) as cur:
            rows = await cur.fetchall()
        if len(rows) <= max_points:
            return [(int(r[0]), float(r[1])) for r in rows]
        step = len(rows) / max_points
        out = [rows[int(i * step)] for i in range(max_points)]
        if out[-1] != rows[-1]:
            out.append(rows[-1])
        return [(int(r[0]), float(r[1])) for r in out]

    async def clear_equity(self, env: str) -> None:
        await self.c.execute("DELETE FROM equity WHERE env=?", (env,))
        await self.c.commit()

    # --- oturumlar -----------------------------------------------------
    async def session_add(self, token_hash: str, created: int, expires: int) -> None:
        await self.c.execute(
            "INSERT OR REPLACE INTO sessions(token_hash, created_at, expires_at) VALUES(?,?,?)",
            (token_hash, created, expires),
        )
        await self.c.commit()

    async def session_valid(self, token_hash: str, now: int) -> bool:
        async with self.c.execute(
            "SELECT expires_at FROM sessions WHERE token_hash=?", (token_hash,)
        ) as cur:
            row = await cur.fetchone()
        return bool(row and int(row[0]) > now)

    async def session_delete(self, token_hash: str) -> None:
        await self.c.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
        await self.c.commit()

    async def session_delete_all(self) -> None:
        await self.c.execute("DELETE FROM sessions")
        await self.c.commit()

    async def session_purge(self, now: int) -> None:
        await self.c.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        await self.c.commit()
