"""N-day breakout swing strategy (CNC delivery, daily candles, long-only).

A close above the prior `lookback_days` daily highs triggers a long entry.
Once in a position, an `trailing_stop_days`-day trailing low acts as the
exit: a close below it triggers an EXIT. One position open per symbol at a
time -- no pyramiding, no shorts (CNC delivery can't go short).
"""
from __future__ import annotations

from collections import deque

from strategy.base import Action, Signal, Strategy


class _SymbolState:
    def __init__(self, lookback_days: int, trailing_stop_days: int):
        self.highs: deque[float] = deque(maxlen=max(1, lookback_days))
        self.lows: deque[float] = deque(maxlen=max(1, trailing_stop_days))
        self.position: bool = False


class BreakoutSwingStrategy(Strategy):
    name = "breakout_swing"

    def __init__(self, lookback_days: int = 20, trailing_stop_days: int = 10):
        self.lookback_days = lookback_days
        self.trailing_stop_days = trailing_stop_days
        self._state: dict[str, _SymbolState] = {}

    def _state_for(self, symbol: str) -> _SymbolState:
        return self._state.setdefault(symbol, _SymbolState(self.lookback_days, self.trailing_stop_days))

    def on_candle(self, symbol: str, candle: dict) -> Signal | None:
        state = self._state_for(symbol)
        close = candle["close"]

        signal: Signal | None = None
        if state.position:
            if state.lows and close < min(state.lows):
                state.position = False
                signal = Signal(self.name, symbol, Action.EXIT, close, reason="trailing stop hit", ts=candle["date"])
        elif len(state.highs) == self.lookback_days and close > max(state.highs):
            state.position = True
            signal = Signal(self.name, symbol, Action.BUY, close, reason=f"close broke {self.lookback_days}-day high", ts=candle["date"])

        state.highs.append(candle["high"])
        state.lows.append(candle["low"])
        return signal
