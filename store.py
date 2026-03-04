"""Persistence layer for hourly paper trading — PostgreSQL or SQLite.

Backend is chosen via environment variable HOURLY_DB:
    HOURLY_DB=postgres  → use PG_HOST/PG_PORT/PG_DB/PG_USER/PG_PASSWORD from .env
    HOURLY_DB=sqlite    → use local file (default: data/hourly_paper.db)
    (unset)             → auto-detect: try PostgreSQL, fall back to SQLite

Tables:
    hourly_paper_kv         — key-value store for engine/state-machine snapshots
    hourly_paper_trades     — closed trade log
    hourly_paper_signals    — signal log
    hourly_paper_equity     — equity curve snapshots
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Placeholder marker for parameterised queries
# ---------------------------------------------------------------------------
_PG = "pg"
_SQLITE = "sqlite"


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _try_pg_conn():
    """Try to create a PostgreSQL connection using .env config."""
    try:
        import psycopg2
        from dotenv import load_dotenv
        load_dotenv()
        conn = psycopg2.connect(
            host=os.environ.get("PG_HOST", "localhost"),
            port=int(os.environ.get("PG_PORT", 5432)),
            dbname=os.environ.get("PG_DB", "crypto_data"),
            user=os.environ.get("PG_USER", "postgres"),
            password=os.environ.get("PG_PASSWORD", ""),
        )
        conn.autocommit = True
        return conn
    except Exception:
        return None


def _sqlite_path() -> Path:
    """Resolve SQLite database path."""
    env = os.environ.get("HOURLY_DB_PATH")
    if env:
        return Path(env)
    return Path(__file__).parent.parent.parent / "data" / "hourly_paper.db"


def _sqlite_conn(path: Path):
    """Create a SQLite connection."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ---------------------------------------------------------------------------
# Unified Store
# ---------------------------------------------------------------------------

class HourlyPaperStore:
    """Persistence store for hourly paper trading.

    Supports both PostgreSQL and SQLite with the same public API.
    """

    def __init__(self) -> None:
        self._conn = None
        self._backend: str = ""  # "pg" or "sqlite"

    @property
    def backend(self) -> str:
        return self._backend

    def connect(self) -> None:
        """Open database connection (auto-detect backend)."""
        choice = os.environ.get("HOURLY_DB", "").lower()

        if choice == "postgres":
            self._conn = _try_pg_conn()
            if not self._conn:
                raise RuntimeError("HOURLY_DB=postgres but connection failed")
            self._backend = _PG
        elif choice == "sqlite":
            self._conn = _sqlite_conn(_sqlite_path())
            self._backend = _SQLITE
        else:
            # Auto-detect: try PG first, fall back to SQLite
            self._conn = _try_pg_conn()
            if self._conn:
                self._backend = _PG
            else:
                self._conn = _sqlite_conn(_sqlite_path())
                self._backend = _SQLITE

        self._create_tables()
        backend_label = "PostgreSQL" if self._backend == _PG else f"SQLite ({_sqlite_path()})"
        logger.info("📦 Hourly paper store: connected to %s", backend_label)

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ph(self, n: int = 1) -> str:
        """Return parameter placeholder(s) for the current backend."""
        if self._backend == _PG:
            return ", ".join(["%s"] * n)
        return ", ".join(["?"] * n)

    def _execute(self, sql: str, params: tuple = ()) -> None:
        """Execute a statement (auto-commit for SQLite)."""
        cur = self._conn.cursor()
        cur.execute(sql, params)
        if self._backend == _SQLITE:
            self._conn.commit()
        cur.close()

    def _fetchone(self, sql: str, params: tuple = ()):
        cur = self._conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        cur.close()
        return row

    def _fetchall(self, sql: str, params: tuple = ()) -> list:
        cur = self._conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows

    def _create_tables(self) -> None:
        """Create tables if they don't exist."""
        # TEXT type works for both PG and SQLite for JSON storage
        stmts = [
            """CREATE TABLE IF NOT EXISTS hourly_paper_kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS hourly_paper_trades (
                id INTEGER PRIMARY KEY {auto_inc},
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                level TEXT,
                entry_price REAL,
                exit_price REAL,
                entry_time TEXT,
                exit_time TEXT,
                pnl REAL,
                pnl_pct REAL,
                exit_reason TEXT,
                hold_hours REAL,
                size_usdt REAL,
                leverage REAL,
                data TEXT,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS hourly_paper_signals (
                id INTEGER PRIMARY KEY {auto_inc},
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                level TEXT,
                base_price REAL,
                entry_price REAL,
                tp_price REAL,
                sl_price REAL,
                leverage REAL,
                signal_time TEXT,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS hourly_paper_equity (
                id INTEGER PRIMARY KEY {auto_inc},
                capital REAL NOT NULL,
                equity REAL NOT NULL,
                positions INTEGER NOT NULL DEFAULT 0,
                timestamp TEXT NOT NULL
            )""",
        ]

        auto_inc = "AUTOINCREMENT" if self._backend == _SQLITE else ""
        # PG uses SERIAL for auto-increment, but INTEGER PRIMARY KEY with
        # no AUTOINCREMENT works with GENERATED ALWAYS for PG >=10.
        # Simplest: just use SERIAL for PG.
        for sql in stmts:
            if self._backend == _PG:
                sql = sql.replace("INTEGER PRIMARY KEY {auto_inc}", "SERIAL PRIMARY KEY")
            else:
                sql = sql.replace("{auto_inc}", auto_inc)
            self._execute(sql)

    # ------------------------------------------------------------------
    # KV store (engine state + state machine)
    # ------------------------------------------------------------------

    def _upsert_kv(self, key: str, value: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        ph = self._ph
        if self._backend == _PG:
            self._execute(
                f"INSERT INTO hourly_paper_kv (key, value, updated_at) "
                f"VALUES ({ph()}, {ph()}, {ph()}) "
                f"ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at",
                (key, value, now),
            )
        else:
            self._execute(
                f"INSERT OR REPLACE INTO hourly_paper_kv (key, value, updated_at) "
                f"VALUES ({ph()}, {ph()}, {ph()})",
                (key, value, now),
            )

    def _get_kv(self, key: str) -> Optional[str]:
        ph = self._ph()
        row = self._fetchone(
            f"SELECT value FROM hourly_paper_kv WHERE key = {ph}", (key,)
        )
        return row[0] if row else None

    def save_engine_state(self, state_dict: dict) -> None:
        self._upsert_kv("engine_state", json.dumps(state_dict))

    def load_engine_state(self) -> Optional[dict]:
        raw = self._get_kv("engine_state")
        if raw is None:
            return None
        return raw if isinstance(raw, dict) else json.loads(raw)

    def save_state_machine(self, states: dict) -> None:
        self._upsert_kv("state_machine", json.dumps(states))

    def load_state_machine(self) -> Optional[dict]:
        raw = self._get_kv("state_machine")
        if raw is None:
            return None
        return raw if isinstance(raw, dict) else json.loads(raw)

    # ------------------------------------------------------------------
    # Trade log
    # ------------------------------------------------------------------

    def save_trade(self, trade: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        ph = self._ph
        self._execute(
            f"INSERT INTO hourly_paper_trades "
            f"(symbol, direction, level, entry_price, exit_price, "
            f" entry_time, exit_time, pnl, pnl_pct, exit_reason, "
            f" hold_hours, size_usdt, leverage, data, created_at) "
            f"VALUES ({ph(15)})",
            (
                trade.get("symbol", ""),
                trade.get("direction", ""),
                trade.get("level", ""),
                trade.get("entry_price", 0),
                trade.get("exit_price", 0),
                trade.get("entry_time", ""),
                trade.get("exit_time", ""),
                trade.get("pnl", 0),
                trade.get("pnl_pct", 0),
                trade.get("exit_reason", ""),
                trade.get("hold_hours", 0),
                trade.get("size_usdt", 0),
                trade.get("leverage", 0),
                json.dumps(trade),
                now,
            ),
        )

    def get_trades(self, limit: int = 20) -> list[dict]:
        rows = self._fetchall(
            f"SELECT data FROM hourly_paper_trades "
            f"ORDER BY id DESC LIMIT {limit}"
        )
        return [json.loads(r[0]) for r in rows]

    # ------------------------------------------------------------------
    # Signal log
    # ------------------------------------------------------------------

    def save_signal(self, signal) -> None:
        """Save a HourlySignal to the signals table."""
        now = datetime.now(timezone.utc).isoformat()
        ph = self._ph
        self._execute(
            f"INSERT INTO hourly_paper_signals "
            f"(symbol, direction, level, base_price, entry_price, "
            f" tp_price, sl_price, leverage, signal_time, created_at) "
            f"VALUES ({ph(10)})",
            (
                signal.symbol,
                signal.direction,
                signal.level,
                signal.base_price,
                signal.entry_price,
                signal.tp_price,
                signal.sl_price,
                signal.leverage,
                signal.signal_time.isoformat() if signal.signal_time else "",
                now,
            ),
        )

    def get_signals(self, limit: int = 20) -> list[dict]:
        rows = self._fetchall(
            "SELECT symbol, direction, level, base_price, entry_price, "
            "tp_price, sl_price, leverage, signal_time "
            f"FROM hourly_paper_signals ORDER BY id DESC LIMIT {limit}"
        )
        return [
            {
                "symbol": r[0], "direction": r[1], "level": r[2],
                "base_price": r[3], "entry_price": r[4],
                "tp_price": r[5], "sl_price": r[6],
                "leverage": r[7], "signal_time": r[8],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Equity snapshots
    # ------------------------------------------------------------------

    def save_equity_snapshot(self, capital: float, equity: float, positions: int = 0) -> None:
        now = datetime.now(timezone.utc).isoformat()
        ph = self._ph
        self._execute(
            f"INSERT INTO hourly_paper_equity (capital, equity, positions, timestamp) "
            f"VALUES ({ph(4)})",
            (capital, equity, positions, now),
        )
