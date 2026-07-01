"""MA9/MA21 + VWAP open-range strategy (intraday, 1-minute candles, MIS).

Entry rules (all must hold, checked on every 1-minute close):
  1. Candle time is between 09:15 and 09:45 (the first 30 minutes of the
     session) -- the open-range window where this setup has the highest
     follow-through probability.
  2. 9-period SMA of close (MA9) > 21-period SMA of close (MA21).
  3. Close price > session VWAP (accumulated from the day's first candle).
  4. No position already open for the symbol today.

Exit rules (whichever fires first):
  - MA9 crosses back below MA21.
  - Close drops back below session VWAP.
  - Hard stop: close <= stop_pct % below the entry close.
  - EOD square-off at settings.square_off_time is handled by the caller
    (run_backtest_ma_vwap in the backtest script), not this class.

VWAP is cumulative (sum(typical_price * volume) / sum(volume)) starting from
the first candle of each day, so it correctly represents session VWAP rather
than a rolling/windowed approximation.  All accumulators reset at midnight.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, time as dt_time

from strategy.base import Action, Signal, Strategy

_OPEN_START = dt_time(9, 15)
_OPEN_END = dt_time(9, 45)


class _SymbolState:
    def __init__(self, ma_slow: int):
        self.closes: deque[float] = deque(maxlen=ma_slow)
        self.cum_pv: float = 0.0
        self.cum_vol: float = 0.0
        self.vwap: float | None = None
        self.position: bool = False
        self.entry_price: float | None = None
        self.stop_price: float | None = None
        self.last_day: str | None = None


class MAVWAPOpenStrategy(Strategy):
    name = "ma_vwap_open"

    def __init__(
        self,
        ma_fast: int = 9,
        ma_slow: int = 21,
        stop_pct: float = 1.0,
    ):
        self.ma_fast = ma_fast
        self.ma_slow = ma_slow
        self.stop_pct = stop_pct
        self._state: dict[str, _SymbolState] = {}

    def _state_for(self, symbol: str) -> _SymbolState:
        return self._state.setdefault(symbol, _SymbolState(self.ma_slow))

    def _day_key(self, ts) -> str:
        if isinstance(ts, datetime):
            return ts.date().isoformat()
        return str(ts)[:10]

    def _candle_time(self, ts) -> dt_time | None:
        if isinstance(ts, datetime):
            return ts.time().replace(second=0, microsecond=0)
        return None

    def on_candle(self, symbol: str, candle: dict) -> Signal | None:
        state = self._state_for(symbol)
        close = candle["close"]
        volume = candle["volume"]
        ts = candle["date"]
        day = self._day_key(ts)
        candle_time = self._candle_time(ts)

        # Reset intraday accumulators on a new day.
        if day != state.last_day:
            state.cum_pv = 0.0
            state.cum_vol = 0.0
            state.vwap = None
            state.position = False
            state.entry_price = None
            state.stop_price = None
            state.last_day = day

        # Update VWAP.
        typical = (candle["high"] + candle["low"] + close) / 3
        state.cum_pv += typical * volume
        state.cum_vol += volume
        vwap = state.cum_pv / state.cum_vol if state.cum_vol > 0 else close
        state.vwap = vwap

        # Update MA buffer.
        state.closes.append(close)

        # Compute MAs only when we have enough history.
        ma_fast: float | None = None
        ma_slow: float | None = None
        if len(state.closes) >= self.ma_slow:
            closes_list = list(state.closes)
            ma_slow = sum(closes_list) / self.ma_slow
            ma_fast = sum(closes_list[-self.ma_fast:]) / self.ma_fast

        signal: Signal | None = None

        if state.position:
            # Exit conditions: VWAP cross-back (primary), MA cross-back,
            # or hard stop.
            if close < vwap:
                state.position = False
                state.entry_price = state.stop_price = None
                signal = Signal(
                    self.name, symbol, Action.EXIT, close,
                    reason="close fell below VWAP", ts=ts,
                )
            elif ma_fast is not None and ma_slow is not None and ma_fast < ma_slow:
                state.position = False
                state.entry_price = state.stop_price = None
                signal = Signal(
                    self.name, symbol, Action.EXIT, close,
                    reason="MA9 crossed below MA21", ts=ts,
                )
            elif state.stop_price is not None and close <= state.stop_price:
                state.position = False
                state.entry_price = state.stop_price = None
                signal = Signal(
                    self.name, symbol, Action.EXIT, close,
                    reason=f"hard stop hit ({self.stop_pct:.1f}%)", ts=ts,
                )

        elif (
            candle_time is not None
            and _OPEN_START <= candle_time <= _OPEN_END
            and ma_fast is not None
            and ma_slow is not None
            and ma_fast > ma_slow
            and close > vwap
        ):
            stop_price = close * (1 - self.stop_pct / 100)
            state.position = True
            state.entry_price = close
            state.stop_price = stop_price
            signal = Signal(
                self.name, symbol, Action.BUY, close,
                reason=f"MA9({ma_fast:.2f})>MA21({ma_slow:.2f}) & close({close:.2f})>VWAP({vwap:.2f})",
                stop_price=stop_price, ts=ts,
            )

        return signal

    def force_exit(self, symbol: str, price: float, ts) -> Signal | None:
        """Called by the backtest runner to square off at EOD."""
        state = self._state.get(symbol)
        if state and state.position:
            state.position = False
            state.entry_price = state.stop_price = None
            return Signal(
                self.name, symbol, Action.EXIT, price,
                reason="EOD square-off", ts=ts,
            )
        return None
