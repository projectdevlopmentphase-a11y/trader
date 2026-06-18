"""Opening Range Breakout strategy.

For each symbol, the first `range_minutes` of trading defines a range
(high/low). Once that range is established, a close above the range high
triggers a long entry and a close below the range low triggers a short
entry. Each symbol takes at most one position per side per day; the risk
manager/order executor is responsible for stop-loss and EOD square-off.
"""
from __future__ import annotations

import re
from datetime import datetime

from strategy.base import Action, Signal, Strategy


def _interval_minutes(interval: str) -> int:
    match = re.match(r"(\d+)\s*minute", interval)
    return int(match.group(1)) if match else 1


class _SymbolState:
    def __init__(self):
        self.day: str | None = None
        self.candles_seen = 0
        self.range_high: float | None = None
        self.range_low: float | None = None
        self.range_ready = False
        self.position: str | None = None  # "LONG" | "SHORT" | None


class ORBStrategy(Strategy):
    name = "orb"

    def __init__(self, range_minutes: int = 15, candle_interval: str = "5minute"):
        self.range_candles = max(1, range_minutes // _interval_minutes(candle_interval))
        self._state: dict[str, _SymbolState] = {}

    def _state_for(self, symbol: str) -> _SymbolState:
        return self._state.setdefault(symbol, _SymbolState())

    def on_candle(self, symbol: str, candle: dict) -> Signal | None:
        state = self._state_for(symbol)
        candle_date = candle["date"]
        day = candle_date.date().isoformat() if isinstance(candle_date, datetime) else str(candle_date)[:10]

        if state.day != day:
            state.day = day
            state.candles_seen = 0
            state.range_high = None
            state.range_low = None
            state.range_ready = False
            state.position = None

        state.candles_seen += 1

        if not state.range_ready:
            state.range_high = candle["high"] if state.range_high is None else max(state.range_high, candle["high"])
            state.range_low = candle["low"] if state.range_low is None else min(state.range_low, candle["low"])
            if state.candles_seen >= self.range_candles:
                state.range_ready = True
            return None

        close = candle["close"]

        if state.position is None:
            if close > state.range_high:
                state.position = "LONG"
                return Signal(self.name, symbol, Action.BUY, close, reason="breakout above opening range high", stop_price=state.range_low)
            if close < state.range_low:
                state.position = "SHORT"
                return Signal(self.name, symbol, Action.SELL, close, reason="breakdown below opening range low", stop_price=state.range_high)
            return None

        if state.position == "LONG" and close < state.range_low:
            state.position = None
            return Signal(self.name, symbol, Action.EXIT, close, reason="reversal below opening range low")
        if state.position == "SHORT" and close > state.range_high:
            state.position = None
            return Signal(self.name, symbol, Action.EXIT, close, reason="reversal above opening range high")

        return None
