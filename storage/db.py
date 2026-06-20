"""SQLite persistence for signals, trades, errors and daily P&L.

Single connection per process, opened lazily. SQLite handles the modest
write volume of an intraday bot fine without a separate DB server.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from config.settings import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    strategy TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    price REAL,
    meta TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    mode TEXT NOT NULL,
    strategy TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    order_id TEXT,
    status TEXT NOT NULL,
    cost REAL NOT NULL DEFAULT 0,
    pnl REAL,
    net_pnl REAL
);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    source TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_pnl (
    trade_date TEXT PRIMARY KEY,
    realized_pnl REAL NOT NULL DEFAULT 0,
    trade_count INTEGER NOT NULL DEFAULT 0,
    halted INTEGER NOT NULL DEFAULT 0
);
"""

_connection: sqlite3.Connection | None = None


def get_connection() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
        _connection = sqlite3.connect(settings.db_path, check_same_thread=False)
        _connection.executescript(_SCHEMA)
        # Existing DBs created before cost/net_pnl tracking was added.
        for column, ddl in (("cost", "REAL NOT NULL DEFAULT 0"), ("net_pnl", "REAL")):
            try:
                _connection.execute(f"ALTER TABLE trades ADD COLUMN {column} {ddl}")
            except sqlite3.OperationalError:
                pass
        _connection.commit()
    return _connection


@contextmanager
def cursor():
    conn = get_connection()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    finally:
        cur.close()


def _format_ts(ts) -> str:
    """Renders a candle's own date/time (market time) as the row's ts,
    falling back to wall-clock time only when no candle time is available
    (e.g. a signal/fill with no associated candle)."""
    if ts is None:
        return datetime.now().isoformat()
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts)


def log_signal(strategy: str, symbol: str, action: str, price: float | None, meta: str = "", ts=None) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO signals (ts, strategy, symbol, action, price, meta) VALUES (?, ?, ?, ?, ?, ?)",
            (_format_ts(ts), strategy, symbol, action, price, meta),
        )


def log_trade(
    mode: str,
    strategy: str,
    symbol: str,
    side: str,
    quantity: int,
    price: float,
    status: str,
    order_id: str | None = None,
    cost: float = 0.0,
    pnl: float | None = None,
    ts=None,
) -> None:
    with cursor() as cur:
        cur.execute(
            """INSERT INTO trades
            (ts, mode, strategy, symbol, side, quantity, price, order_id, status, cost, pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _format_ts(ts),
                mode,
                strategy,
                symbol,
                side,
                quantity,
                price,
                order_id,
                status,
                cost,
                pnl,
            ),
        )


def update_last_trade_pnl(symbol: str, pnl: float, net_pnl: float | None = None) -> None:
    """Stamps the realized gross (and net, after transaction costs) P&L onto
    the most recently logged trade row for this symbol (the exit fill just
    recorded by log_trade)."""
    with cursor() as cur:
        cur.execute(
            """UPDATE trades SET pnl = ?, net_pnl = ? WHERE id = (
                SELECT id FROM trades WHERE symbol = ? ORDER BY id DESC LIMIT 1
            )""",
            (pnl, net_pnl if net_pnl is not None else pnl, symbol),
        )


def log_error(source: str, message: str) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO errors (ts, source, message) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), source, message),
        )


def update_daily_pnl(trade_date: str, pnl_delta: float) -> float:
    """Adds pnl_delta to today's running total and returns the new total."""
    with cursor() as cur:
        cur.execute(
            """INSERT INTO daily_pnl (trade_date, realized_pnl, trade_count)
            VALUES (?, ?, 1)
            ON CONFLICT(trade_date) DO UPDATE SET
                realized_pnl = realized_pnl + excluded.realized_pnl,
                trade_count = trade_count + 1""",
            (trade_date, pnl_delta),
        )
        cur.execute("SELECT realized_pnl FROM daily_pnl WHERE trade_date = ?", (trade_date,))
        return cur.fetchone()[0]


def get_daily_pnl(trade_date: str) -> float:
    with cursor() as cur:
        cur.execute("SELECT realized_pnl FROM daily_pnl WHERE trade_date = ?", (trade_date,))
        row = cur.fetchone()
        return row[0] if row else 0.0


def set_halted(trade_date: str, halted: bool) -> None:
    with cursor() as cur:
        cur.execute(
            """INSERT INTO daily_pnl (trade_date, realized_pnl, trade_count, halted)
            VALUES (?, 0, 0, ?)
            ON CONFLICT(trade_date) DO UPDATE SET halted = excluded.halted""",
            (trade_date, int(halted)),
        )


def is_halted(trade_date: str) -> bool:
    with cursor() as cur:
        cur.execute("SELECT halted FROM daily_pnl WHERE trade_date = ?", (trade_date,))
        row = cur.fetchone()
        return bool(row[0]) if row else False


def reset_backtest_data() -> None:
    """Wipes signals/trades/daily_pnl so a backtest re-run starts from a clean slate
    instead of accumulating on top of a previous run's rows."""
    with cursor() as cur:
        cur.execute("DELETE FROM signals")
        cur.execute("DELETE FROM trades")
        cur.execute("DELETE FROM daily_pnl")
        cur.execute("DELETE FROM errors")
