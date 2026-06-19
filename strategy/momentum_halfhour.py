"""First-half-hour -> last-half-hour momentum strategy.

At the close of the first `signal_window_minutes` (default 30) of the
session, compares price to the prior day's close: if up, go long; if down,
go short. One entry per symbol per day, at the window's close. Holds until
EOD square-off -- this strategy never emits its own EXIT, same as relying
on the existing scheduler/backtest square-off logic ORBStrategy also
depends on.

Entries are optionally gated by a volume filter: the opening window's
volume must be >= volume_multiplier times the trailing volume_lookback
days' average opening-window volume (set volume_multiplier to 0 to
disable). In backtest, the caller precomputes this per day from each
symbol's full historical series (see scanner.compute_volume_ok_days) and
stamps it onto the candle as "_volume_ok", since this strategy instance
only ever sees candles for days a symbol was actually selected into the
watchlist -- not enough to track the volume baseline on its own.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime

from strategy.base import Action, Signal, Strategy


class _SymbolState:
    def __init__(self, volume_lookback: int):
        self.day: str | None = None
        self.session_start: datetime | None = None
        self.last_close: float | None = None
        self.prior_close: float | None = None
        self.signaled_today = False
        self.window_volume = 0.0
        self.window_volumes: deque[float] = deque(maxlen=max(1, volume_lookback))


class MomentumHalfHourStrategy(Strategy):
    name = "momentum_halfhour"

    def __init__(
        self,
        signal_window_minutes: int = 30,
        volume_multiplier: float = 0,
        volume_lookback: int = 20,
    ):
        self.signal_window_minutes = signal_window_minutes
        self.volume_multiplier = volume_multiplier
        self.volume_lookback = volume_lookback
        self._state: dict[str, _SymbolState] = {}

    def _state_for(self, symbol: str) -> _SymbolState:
        return self._state.setdefault(symbol, _SymbolState(self.volume_lookback))

    def on_candle(self, symbol: str, candle: dict) -> Signal | None:
        state = self._state_for(symbol)
        candle_date = candle["date"]
        is_dt = isinstance(candle_date, datetime)
        day = candle_date.date().isoformat() if is_dt else str(candle_date)[:10]

        if state.day != day:
            if state.last_close is not None:
                state.prior_close = state.last_close
            state.day = day
            state.session_start = candle_date if is_dt else None
            state.signaled_today = False
            state.window_volume = 0.0

        elapsed_minutes = (
            (candle_date - state.session_start).total_seconds() / 60
            if is_dt and state.session_start is not None
            else 0
        )

        signal: Signal | None = None
        if elapsed_minutes < self.signal_window_minutes:
            state.window_volume += candle["volume"]
        elif not state.signaled_today:
            state.signaled_today = True

            external_volume_ok = candle.get("_volume_ok")
            if external_volume_ok is not None:
                volume_ok = external_volume_ok
            elif self.volume_multiplier > 0 and state.window_volumes:
                avg_volume = sum(state.window_volumes) / len(state.window_volumes)
                volume_ok = state.window_volume >= self.volume_multiplier * avg_volume
            else:
                volume_ok = True
            state.window_volumes.append(state.window_volume)

            if state.prior_close:
                first_half_hour_return = (candle["close"] - state.prior_close) / state.prior_close
                if volume_ok and first_half_hour_return != 0:
                    action = Action.BUY if first_half_hour_return > 0 else Action.SELL
                    reason = f"first-{self.signal_window_minutes}min return {first_half_hour_return:.4f}"
                    signal = Signal(self.name, symbol, action, candle["close"], reason=reason, ts=candle_date)

        state.last_close = candle["close"]
        return signal
