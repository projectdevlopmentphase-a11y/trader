"""Pushes a full snapshot of the local SQLite trading data to Cloudflare D1.

Local SQLite (storage/db.py) is the source of truth — the bot never writes
to D1 directly. This module is only invoked on demand (via the /sync HTTP
endpoint in api/server.py, triggered by the dashboard's Refresh button) to
replace D1's contents with whatever is currently in trader.db.

Given the low data volume of a personal intraday bot, a full
delete-and-reload per table is simpler and more robust than incremental
syncing and is cheap enough to run on every click.
"""
from __future__ import annotations

import requests

from config.settings import settings
from storage.db import get_connection

D1_API_BASE = "https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query"

TABLES = {
    "signals": ["id", "ts", "strategy", "symbol", "action", "price", "meta"],
    "trades": ["id", "ts", "mode", "strategy", "symbol", "side", "quantity", "price", "order_id", "status", "pnl"],
    "errors": ["id", "ts", "source", "message"],
    "daily_pnl": ["trade_date", "realized_pnl", "trade_count", "halted"],
}


class D1SyncError(RuntimeError):
    pass


def _d1_query(sql: str, params: list | None = None) -> dict:
    if not (settings.cf_account_id and settings.cf_api_token and settings.cf_d1_database_id):
        raise D1SyncError("CF_ACCOUNT_ID / CF_API_TOKEN / CF_D1_DATABASE_ID must be set")

    url = D1_API_BASE.format(account_id=settings.cf_account_id, database_id=settings.cf_d1_database_id)
    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {settings.cf_api_token}",
            "Content-Type": "application/json",
        },
        json={"sql": sql, "params": params or []},
        timeout=30,
    )
    data = response.json()
    if not response.ok or not data.get("success", False):
        raise D1SyncError(f"D1 query failed: {data.get('errors') or response.text}")
    return data


def _fetch_local_rows(conn, table: str, columns: list[str]) -> list[tuple]:
    cur = conn.cursor()
    cur.execute(f"SELECT {', '.join(columns)} FROM {table}")
    rows = cur.fetchall()
    cur.close()
    return rows


# D1 caps bound parameters per statement at 100, so wide tables need batched inserts.
MAX_PARAMS_PER_QUERY = 100


def _push_table(table: str, columns: list[str], rows: list[tuple]) -> int:
    _d1_query(f"DELETE FROM {table}")
    if not rows:
        return 0

    batch_size = max(1, MAX_PARAMS_PER_QUERY // len(columns))
    placeholders = "(" + ", ".join(["?"] * len(columns)) + ")"

    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        values_sql = ", ".join([placeholders] * len(batch))
        flat_params = [value for row in batch for value in row]
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES {values_sql}"
        _d1_query(sql, flat_params)

    return len(rows)


def sync_to_d1() -> dict[str, int]:
    """Replaces D1's contents with the current local SQLite data. Returns row counts per table."""
    conn = get_connection()
    counts = {}
    for table, columns in TABLES.items():
        rows = _fetch_local_rows(conn, table, columns)
        counts[table] = _push_table(table, columns, rows)
    return counts


if __name__ == "__main__":
    print(sync_to_d1())
