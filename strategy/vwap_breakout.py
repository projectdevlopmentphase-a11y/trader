"""Intraday VWAP breakout strategy (MIS, 5-minute candles, EOD square-off).

VWAP (Volume-Weighted Average Price) is recomputed from the first candle of
each trading day (cumulative, not rolling) so it represents the true
session VWAP rather than a windowed approximation.

Entry rule: the previous candle closed *below* VWAP and the current candle
closes *above* VWAP (confirmed bullish cross) AND the current candle's
volume exceeds the average volume of the preceding `volume_lookback` candles
by `volume_multiplier` -- the volume filter avoids low-conviction crosses
on thin trading.

To avoid the noisy open, no signals are generated for the first
`min_candles_after_open` candles of the session (default 6 = 30 minutes on
5-minute bars).

Exit rule: price closes back below VWAP → EXIT. A hard stop (stop_pct below
entry price) guards against gaps that skip the VWAP cross exit. The caller
(main.py) forces an EOD square-off at settings.square_off_time independent
of this strategy.

One position per symbol at a time, long-only (MIS/intraday -- can't hold
overnight delivery on a VWAP signal).
"""
from __future__ import annotations

from collections import deque
from datetime import datetime

from strategy.base import Action, Signal, Strategy


class _SymbolState:
    def __init__(self, volume_lookback: int):
        self.cum_pv: float = 0.0        # cumulative price * volume
        self.cum_vol: float = 0.0       # cumulative volume
        self.vwap: float | None = None
        self.prev_above: bool | None = None   # was previous close above VWAP?
        self.position: bool = False
        self.entry_price: float | None = None
        self.stop_price: float | None = None
        self.candle_count: int = 0      # candles seen today (for open filter)
        self.recent_volumes: deque[float] = deque(maxlen=volume_lookback)
        self.last_day: str | None = None


class VWAPBreakoutStrategy(Strategy):
    name = "vwap_breakout"

    def __init__(
        self,
        stop_pct: float = 1.5,
        min_candles_after_open: int = 6,
        volume_multiplier: float = 1.5,
        volume_lookback: int = 10,
    ):
        self.stop_pct = stop_pct
        self.min_candles_after_open = min_candles_after_open
        self.volume_multiplier = volume_multiplier
        self.volume_lookback = volume_lookback
        self._state: dict[str, _SymbolState] = {}

    def _state_for(self, symbol: str) -> _SymbolState:
        return self._state.setdefault(symbol, _SymbolState(self.volume_lookback))

    def _day_key(self, candle_date) -> str:
        if isinstance(candle_date, datetime):
            return candle_date.date().isoformat()
        return str(candle_date)[:10]

    def on_candle(self, symbol: str, candle: dict) -> Signal | None:
        state = self._state_for(symbol)
        close = candle["close"]
        volume = candle["volume"]
        ts = candle["date"]
        day = self._day_key(ts)

        # New day — reset all intraday accumulators but carry over volume
        # history so the baseline isn't empty on the first cross.
        if day != state.last_day:
            state.cum_pv = 0.0
            state.cum_vol = 0.0
            state.vwap = None
            state.prev_above = None
            state.candle_count = 0
            state.last_day = day
            # Don't reset position or stop — EOD square-off is the caller's job.

        typical_price = (candle["high"] + candle["low"] + close) / 3
        state.cum_pv += typical_price * volume
        state.cum_vol += volume
        state.vwap = state.cum_pv / state.cum_vol if state.cum_vol > 0 else close
        state.candle_count += 1

        above_vwap = close > state.vwap
        signal: Signal | None = None

        if state.position:
            # VWAP-cross exit is the primary signal; the hard stop is a
            # backstop for gap-downs that skip the VWAP level entirely.
            if not above_vwap:
                state.position = False
                state.entry_price = None
                state.stop_price = None
                signal = Signal(
                    self.name, symbol, Action.EXIT, close,
                    reason="price crossed back below VWAP", ts=ts,
                )
            elif state.stop_price is not None and close <= state.stop_price:
                state.position = False
                state.entry_price = None
                state.stop_price = None
                signal = Signal(
                    self.name, symbol, Action.EXIT, close,
                    reason=f"stop loss hit ({self.stop_pct:.1f}%)", ts=ts,
                )
        else:
            # Entry: previous candle was below VWAP, this one is above, past
            # the open filter, and volume exceeds the recent average.
            if (
                state.prev_above is not None
                and not state.prev_above
                and above_vwap
                and state.candle_count > self.min_candles_after_open
            ):
                avg_vol = (sum(state.recent_volumes) / len(state.recent_volumes)) if state.recent_volumes else 0.0
                if avg_vol == 0 or volume >= avg_vol * self.volume_multiplier:
                    stop_price = close * (1 - self.stop_pct / 100)
                    state.position = True
                    state.entry_price = close
                    state.stop_price = stop_price
                    signal = Signal(
                        self.name, symbol, Action.BUY, close,
                        reason="price crossed above VWAP with volume confirmation",
                        stop_price=stop_price, ts=ts,
                    )

        state.prev_above = above_vwap
        state.recent_volumes.append(volume)
        return signal
