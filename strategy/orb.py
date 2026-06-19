"""Opening Range Breakout strategy.

For each symbol, the first `range_minutes` of trading defines a range
(high/low). Once that range is established, a close above the range high
triggers a long entry and a close below the range low triggers a short
entry. Each symbol takes at most one position per side per day; the risk
manager/order executor is responsible for stop-loss and EOD square-off.

Entries are additionally gated by three filters (each independently
disabled by setting its threshold to 0/None):
- Volume confirmation: breakout candle's volume must be >= volume_multiplier
  times the recent average volume.
- NR7-style range filter: only enter on a day whose prior day's range was
  the narrowest of the last nr7_lookback trading days.
- Time-of-day cutoff: no new entries after entry_cutoff_time (exits/EOD
  square-off are unaffected).
"""
from __future__ import annotations

import re
from collections import deque
from datetime import datetime, time as dt_time

from strategy.base import Action, Signal, Strategy


def _interval_minutes(interval: str) -> int:
    match = re.match(r"(\d+)\s*minute", interval)
    return int(match.group(1)) if match else 1


def _parse_time(value: str | None) -> dt_time | None:
    if not value:
        return None
    hour, minute = value.split(":")
    return dt_time(int(hour), int(minute))


class _SymbolState:
    def __init__(self, nr7_lookback: int, volume_lookback: int):
        self.day: str | None = None
        self.candles_seen = 0
        self.range_high: float | None = None
        self.range_low: float | None = None
        self.range_ready = False
        self.position: str | None = None  # "LONG" | "SHORT" | None
        self.stop_price: float | None = None
        self.took_long = False
        self.took_short = False
        self.day_high: float | None = None
        self.day_low: float | None = None
        self.volumes: deque[float] = deque(maxlen=max(1, volume_lookback))
        self.day_ranges: deque[float] = deque(maxlen=max(1, nr7_lookback))
        self.narrow_range_day = True  # whether today qualifies for entries


class ORBStrategy(Strategy):
    name = "orb"

    def __init__(
        self,
        range_minutes: int = 15,
        candle_interval: str = "5minute",
        volume_multiplier: float = 1.5,
        volume_lookback: int = 20,
        nr7_lookback: int = 7,
        entry_cutoff_time: str | None = "13:00",
    ):
        self.range_candles = max(1, range_minutes // _interval_minutes(candle_interval))
        self.volume_multiplier = volume_multiplier
        self.volume_lookback = volume_lookback
        self.nr7_lookback = nr7_lookback
        self.entry_cutoff = _parse_time(entry_cutoff_time)
        self._state: dict[str, _SymbolState] = {}

    def _state_for(self, symbol: str) -> _SymbolState:
        return self._state.setdefault(symbol, _SymbolState(self.nr7_lookback, self.volume_lookback))

    def on_candle(self, symbol: str, candle: dict) -> Signal | None:
        state = self._state_for(symbol)
        candle_date = candle["date"]
        is_dt = isinstance(candle_date, datetime)
        day = candle_date.date().isoformat() if is_dt else str(candle_date)[:10]
        candle_time = candle_date.time() if is_dt else None

        if state.day != day:
            if state.day is not None and state.day_high is not None and state.day_low is not None:
                state.day_ranges.append(state.day_high - state.day_low)
            if self.nr7_lookback > 0:
                state.narrow_range_day = (
                    len(state.day_ranges) == self.nr7_lookback
                    and state.day_ranges[-1] == min(state.day_ranges)
                )
            else:
                state.narrow_range_day = True

            state.day = day
            state.candles_seen = 0
            state.range_high = None
            state.range_low = None
            state.range_ready = False
            state.position = None
            state.stop_price = None
            state.took_long = False
            state.took_short = False
            state.day_high = None
            state.day_low = None

        state.day_high = candle["high"] if state.day_high is None else max(state.day_high, candle["high"])
        state.day_low = candle["low"] if state.day_low is None else min(state.day_low, candle["low"])
        state.candles_seen += 1

        if not state.range_ready:
            state.range_high = candle["high"] if state.range_high is None else max(state.range_high, candle["high"])
            state.range_low = candle["low"] if state.range_low is None else min(state.range_low, candle["low"])
            state.volumes.append(candle["volume"])
            if state.candles_seen >= self.range_candles:
                state.range_ready = True
            return None

        close = candle["close"]
        volume = candle["volume"]

        if self.volume_multiplier > 0 and state.volumes:
            avg_volume = sum(state.volumes) / len(state.volumes)
            volume_ok = volume >= self.volume_multiplier * avg_volume
        else:
            volume_ok = True
        state.volumes.append(volume)

        cutoff_ok = self.entry_cutoff is None or candle_time is None or candle_time <= self.entry_cutoff
        # In backtest, the caller precomputes narrow-range-day from each
        # symbol's full historical series (see scanner.compute_narrow_range_days)
        # and stamps it on the candle, since this strategy instance only ever
        # sees candles for days a symbol was actually selected into the
        # watchlist -- not enough to track NR7 history on its own.
        external_narrow_range_day = candle.get("_narrow_range_day")
        narrow_range_ok = state.narrow_range_day if external_narrow_range_day is None else external_narrow_range_day
        entries_allowed = volume_ok and narrow_range_ok and cutoff_ok

        if state.position is None:
            if entries_allowed and close > state.range_high and not state.took_long:
                state.position = "LONG"
                state.took_long = True
                state.stop_price = state.range_low
                return Signal(self.name, symbol, Action.BUY, close, reason="breakout above opening range high", stop_price=state.range_low, ts=candle_date)
            if entries_allowed and close < state.range_low and not state.took_short:
                state.position = "SHORT"
                state.took_short = True
                state.stop_price = state.range_high
                return Signal(self.name, symbol, Action.SELL, close, reason="breakdown below opening range low", stop_price=state.range_high, ts=candle_date)
            return None

        # Intrabar stop: exit as soon as the candle's high/low touches the
        # stop level, rather than waiting for a close beyond it -- a close-
        # only check lets a position ride through the whole candle even
        # after the stop has already been breached intraday.
        if state.position == "LONG" and candle["low"] <= state.stop_price:
            state.position = None
            return Signal(self.name, symbol, Action.EXIT, state.stop_price, reason="stop-loss hit", ts=candle_date)
        if state.position == "SHORT" and candle["high"] >= state.stop_price:
            state.position = None
            return Signal(self.name, symbol, Action.EXIT, state.stop_price, reason="stop-loss hit", ts=candle_date)

        return None
