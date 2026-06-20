"""Historical candle fetch for backtesting via the Kite Connect REST API."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from kiteconnect import KiteConnect

_INSTRUMENTS_CACHE_DIR = Path(__file__).resolve().parent.parent / "reports" / "instruments_cache"
_INSTRUMENTS_CACHE_TTL_SECONDS = 24 * 60 * 60

# Kite's historical API is capped at 3 requests/second; pace every outgoing
# request (chunks within one call, and successive calls across symbols)
# below that, tracked globally so callers looping over symbols don't need to
# pace themselves.
_REQUEST_INTERVAL_SECONDS = 0.4
_last_request_time: float | None = None


def _throttle() -> None:
    global _last_request_time
    now = time.monotonic()
    if _last_request_time is not None:
        elapsed = now - _last_request_time
        if elapsed < _REQUEST_INTERVAL_SECONDS:
            time.sleep(_REQUEST_INTERVAL_SECONDS - elapsed)
    _last_request_time = time.monotonic()

# Kite's historical API caps the date range per request depending on the
# candle interval; requesting a wider span raises
# "interval exceeds max limit: N days". Values per Kite Connect docs.
_MAX_DAYS_PER_REQUEST = {
    "minute": 60,
    "3minute": 100,
    "5minute": 100,
    "10minute": 100,
    "15minute": 200,
    "30minute": 200,
    "60minute": 400,
    "day": 2000,
}


def fetch_historical_candles(
    kite: KiteConnect,
    instrument_token: int,
    from_date: datetime,
    to_date: datetime,
    interval: str = "5minute",
) -> list[dict]:
    """Returns a list of OHLC candle dicts as provided by Kite's historical API.

    Each item has keys: date, open, high, low, close, volume.

    Splits the request into chunks no wider than the interval's max allowed
    range, since Kite rejects a single request spanning too many days.
    """
    max_days = _MAX_DAYS_PER_REQUEST.get(interval, 100)
    candles: list[dict] = []
    seen_dates: set = set()
    chunk_start = from_date
    while chunk_start < to_date:
        _throttle()
        chunk_end = min(chunk_start + timedelta(days=max_days), to_date)
        for candle in kite.historical_data(instrument_token, chunk_start, chunk_end, interval):
            if candle["date"] not in seen_dates:
                seen_dates.add(candle["date"])
                candles.append(candle)
        chunk_start = chunk_end
    return candles


def fetch_instruments(kite: KiteConnect, exchange: str = "NSE") -> list[dict]:
    """Returns the exchange's instrument master, cached to disk for
    _INSTRUMENTS_CACHE_TTL_SECONDS.

    The instrument master (tradingsymbol/instrument_token mapping) barely
    changes day to day but is a heavy call -- without caching, every
    backtest/screen run (and every combination in a parameter sweep) burns
    a full instrument dump on top of its historical candle requests, which
    is a major contributor to hitting Kite's rate limit during sweeps.
    """
    cache_path = _INSTRUMENTS_CACHE_DIR / f"{exchange}.json"
    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < _INSTRUMENTS_CACHE_TTL_SECONDS:
            with open(cache_path) as f:
                return json.load(f)

    _throttle()
    instruments = kite.instruments(exchange)
    _INSTRUMENTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(instruments, f, default=str)
    return instruments
