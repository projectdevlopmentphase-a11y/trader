"""Historical candle fetch for backtesting via the Kite Connect REST API."""
from __future__ import annotations

from datetime import datetime, timedelta

from kiteconnect import KiteConnect

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
        chunk_end = min(chunk_start + timedelta(days=max_days), to_date)
        for candle in kite.historical_data(instrument_token, chunk_start, chunk_end, interval):
            if candle["date"] not in seen_dates:
                seen_dates.add(candle["date"])
                candles.append(candle)
        chunk_start = chunk_end
    return candles
