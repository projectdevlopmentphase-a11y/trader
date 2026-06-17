"""Aggregates live ticks into fixed-interval OHLC candles.

Used by the paper and live modes, which only have access to a raw tick
stream (KiteTicker) and need to feed the strategy engine the same
candle-shaped data the backtest mode gets from the historical API.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable


class Candle:
    def __init__(self, symbol: str, start: datetime, price: float, volume: int = 0):
        self.symbol = symbol
        self.start = start
        self.open = price
        self.high = price
        self.low = price
        self.close = price
        self.volume = volume

    def update(self, price: float, volume: int = 0) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += volume

    def as_dict(self) -> dict:
        return {
            "date": self.start,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


class CandleBuilder:
    """Buckets ticks per symbol into candles of `interval_seconds` length.

    Calls `on_candle_close(symbol, candle_dict)` whenever a bucket boundary
    is crossed, i.e. as soon as the previous candle is known to be complete.
    """

    def __init__(self, interval_seconds: int, on_candle_close: Callable[[str, dict], None]):
        self.interval_seconds = interval_seconds
        self.on_candle_close = on_candle_close
        self._current: dict[str, Candle] = {}

    def _bucket_start(self, ts: datetime) -> datetime:
        epoch_seconds = int(ts.timestamp())
        bucket_seconds = epoch_seconds - (epoch_seconds % self.interval_seconds)
        return datetime.fromtimestamp(bucket_seconds)

    def on_tick(self, symbol: str, price: float, volume: int = 0, ts: datetime | None = None) -> None:
        ts = ts or datetime.now()
        bucket_start = self._bucket_start(ts)
        candle = self._current.get(symbol)

        if candle is None:
            self._current[symbol] = Candle(symbol, bucket_start, price, volume)
            return

        if bucket_start > candle.start:
            self.on_candle_close(symbol, candle.as_dict())
            self._current[symbol] = Candle(symbol, bucket_start, price, volume)
        else:
            candle.update(price, volume)

    def flush(self, symbol: str) -> None:
        """Force-closes the in-progress candle, e.g. at end of day."""
        candle = self._current.pop(symbol, None)
        if candle is not None:
            self.on_candle_close(symbol, candle.as_dict())
