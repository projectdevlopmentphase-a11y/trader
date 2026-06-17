"""Historical candle fetch for backtesting via the Kite Connect REST API."""
from __future__ import annotations

from datetime import datetime

from kiteconnect import KiteConnect


def fetch_historical_candles(
    kite: KiteConnect,
    instrument_token: int,
    from_date: datetime,
    to_date: datetime,
    interval: str = "5minute",
) -> list[dict]:
    """Returns a list of OHLC candle dicts as provided by Kite's historical API.

    Each item has keys: date, open, high, low, close, volume.
    """
    return kite.historical_data(instrument_token, from_date, to_date, interval)
