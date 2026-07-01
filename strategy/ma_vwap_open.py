"""MA9/MA21 + VWAP open-range strategy (intraday, 1-minute candles, MIS).

Entry rules (all must hold on every 1-minute close in the 09:15–09:45 window):
  1. MA9 (9-period SMA of close) > MA21 (21-period SMA of close).
  2. Close price > VWAP reference (see exit_mode below).
  3. No position already open for the symbol today.

Three exit modes (set via exit_mode param):

  "vwap"      — original mode. Both entry filter and exit signal use the
                 *current session* VWAP accumulated from today's first candle.
                 Exit fires when close falls back below session VWAP, MA9
                 crosses below MA21, or the hard stop is hit.  Early-session
                 VWAP is noisy so this tends to produce many quick exit
                 signals right after entry.

  "target"    — entry filter still uses current session VWAP, but exit is a
                 fixed reward:risk target (close >= entry + target_r * stop_dist)
                 rather than a VWAP recross.  MA9<MA21 cross and hard stop
                 remain as secondary exits.  Lets winners run past the first
                 VWAP wobble.

  "prev_vwap" — entry filter uses the *previous session's closing VWAP*
                 instead of today's intraday VWAP.  Previous-day VWAP is
                 stable and meaningful as a price-level reference; price
                 opening above it signals genuine overnight strength.  Exit
                 reverts to MA9<MA21 cross, close < prev_day_vwap, or hard
                 stop.

EOD square-off is always delegated to the caller via force_exit().
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, time as dt_time

from strategy.base import Action, Signal, Strategy

_OPEN_START = dt_time(9, 15)
_OPEN_END   = dt_time(9, 45)


class _SymbolState:
    def __init__(self, ma_slow: int):
        self.closes: deque[float] = deque(maxlen=ma_slow)
        # current-session VWAP accumulators
        self.cum_pv: float = 0.0
        self.cum_vol: float = 0.0
        self.vwap: float | None = None
        # previous-session closing VWAP
        self.prev_vwap: float | None = None
        self._prev_cum_pv: float = 0.0
        self._prev_cum_vol: float = 0.0
        # position state
        self.position: bool = False
        self.entry_price: float | None = None
        self.stop_price: float | None = None
        self.target_price: float | None = None
        self.last_day: str | None = None


class MAVWAPOpenStrategy(Strategy):
    name = "ma_vwap_open"

    def __init__(
        self,
        ma_fast: int = 9,
        ma_slow: int = 21,
        stop_pct: float = 1.0,
        exit_mode: str = "vwap",   # "vwap" | "target" | "prev_vwap"
        target_r: float = 2.0,     # reward multiple used when exit_mode="target"
    ):
        if exit_mode not in ("vwap", "target", "prev_vwap"):
            raise ValueError(f"exit_mode must be 'vwap', 'target', or 'prev_vwap', got {exit_mode!r}")
        self.ma_fast = ma_fast
        self.ma_slow = ma_slow
        self.stop_pct = stop_pct
        self.exit_mode = exit_mode
        self.target_r = target_r
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
        close  = candle["close"]
        volume = candle["volume"]
        ts     = candle["date"]
        day    = self._day_key(ts)
        t      = self._candle_time(ts)

        # New day: roll previous-session VWAP, reset intraday accumulators.
        if day != state.last_day:
            if state.cum_vol > 0:
                state.prev_vwap = state.cum_pv / state.cum_vol
            state.cum_pv  = 0.0
            state.cum_vol = 0.0
            state.vwap    = None
            state.position     = False
            state.entry_price  = None
            state.stop_price   = None
            state.target_price = None
            state.last_day = day

        # Accumulate today's VWAP.
        typical       = (candle["high"] + candle["low"] + close) / 3
        state.cum_pv  += typical * volume
        state.cum_vol += volume
        today_vwap    = state.cum_pv / state.cum_vol if state.cum_vol > 0 else close
        state.vwap    = today_vwap

        # Update MA buffer.
        state.closes.append(close)
        ma_fast: float | None = None
        ma_slow: float | None = None
        if len(state.closes) >= self.ma_slow:
            closes_list = list(state.closes)
            ma_slow = sum(closes_list) / self.ma_slow
            ma_fast = sum(closes_list[-self.ma_fast:]) / self.ma_fast

        signal: Signal | None = None

        if state.position:
            signal = self._check_exit(state, close, today_vwap, ma_fast, ma_slow, ts)
        elif (
            t is not None
            and _OPEN_START <= t <= _OPEN_END
            and ma_fast is not None
            and ma_slow is not None
            and ma_fast > ma_slow
        ):
            signal = self._check_entry(state, close, today_vwap, ma_fast, ma_slow, ts)

        return signal

    def _vwap_ref(self, state: _SymbolState, today_vwap: float) -> float | None:
        """Returns the VWAP value used as the price-level reference for this
        mode.  None means we don't yet have enough history (prev_vwap mode
        on the very first day)."""
        if self.exit_mode == "prev_vwap":
            return state.prev_vwap   # may be None on first day
        return today_vwap

    def _check_entry(
        self,
        state: _SymbolState,
        close: float,
        today_vwap: float,
        ma_fast: float,
        ma_slow: float,
        ts,
    ) -> Signal | None:
        ref = self._vwap_ref(state, today_vwap)
        if ref is None or close <= ref:
            return None

        stop_price  = close * (1 - self.stop_pct / 100)
        stop_dist   = close - stop_price
        target_price = close + self.target_r * stop_dist if self.exit_mode == "target" else None

        state.position     = True
        state.entry_price  = close
        state.stop_price   = stop_price
        state.target_price = target_price

        ref_label = "prev_VWAP" if self.exit_mode == "prev_vwap" else "VWAP"
        return Signal(
            self.name, state.last_day, Action.BUY, close,
            reason=(
                f"MA{self.ma_fast}({ma_fast:.2f})>MA{self.ma_slow}({ma_slow:.2f})"
                f" & close({close:.2f})>{ref_label}({ref:.2f})"
            ),
            stop_price=stop_price, ts=ts,
        )

    def _check_exit(
        self,
        state: _SymbolState,
        close: float,
        today_vwap: float,
        ma_fast: float | None,
        ma_slow: float | None,
        ts,
    ) -> Signal | None:
        def _exit(reason: str) -> Signal:
            state.position = state.entry_price = state.stop_price = state.target_price = None
            return Signal(self.name, state.last_day, Action.EXIT, close, reason=reason, ts=ts)

        if self.exit_mode == "target":
            # Primary exit: profit target reached.
            if state.target_price is not None and close >= state.target_price:
                return _exit(f"profit target hit ({self.target_r:.1f}R)")
            # Secondary: MA cross-back.
            if ma_fast is not None and ma_slow is not None and ma_fast < ma_slow:
                return _exit("MA9 crossed below MA21")
            # Backstop: hard stop.
            if state.stop_price is not None and close <= state.stop_price:
                return _exit(f"hard stop hit ({self.stop_pct:.1f}%)")

        elif self.exit_mode == "prev_vwap":
            ref = state.prev_vwap
            if ref is not None and close < ref:
                return _exit("close fell below prev-day VWAP")
            if ma_fast is not None and ma_slow is not None and ma_fast < ma_slow:
                return _exit("MA9 crossed below MA21")
            if state.stop_price is not None and close <= state.stop_price:
                return _exit(f"hard stop hit ({self.stop_pct:.1f}%)")

        else:  # "vwap"
            if close < today_vwap:
                return _exit("close fell below VWAP")
            if ma_fast is not None and ma_slow is not None and ma_fast < ma_slow:
                return _exit("MA9 crossed below MA21")
            if state.stop_price is not None and close <= state.stop_price:
                return _exit(f"hard stop hit ({self.stop_pct:.1f}%)")

        return None

    def force_exit(self, symbol: str, price: float, ts) -> Signal | None:
        """Square off an open position at EOD; called by the backtest runner."""
        state = self._state.get(symbol)
        if state and state.position:
            state.position = state.entry_price = state.stop_price = state.target_price = None
            return Signal(
                self.name, symbol, Action.EXIT, price,
                reason="EOD square-off", ts=ts,
            )
        return None
