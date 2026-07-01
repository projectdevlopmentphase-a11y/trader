"""ORB + Trend Confirmation (intraday, 1-minute candles, MIS, long & short).

Setup
─────
09:15–09:30  Mark opening-range high and low from 1-min candles.
09:30–09:45  Breakout detection window.

Entry (Long)
─────────────
• Close breaks above ORB high (close, not wick).
• MA9 > MA21.
• Close > session VWAP.
• Volume of the breakout candle > 1.5× average of the 5 preceding candles.

Entry (Short) — mirror logic, reversed direction.

Filters (skip the day / signal)
────────────────────────────────
• ORB range < 0.3% of ORB low  → too tight, low reward.
• ORB range > 1.5% of ORB low  → too volatile, bad R:R.
• No entries in the first 2 candles after 09:15 (9:15–9:16 are spread noise;
  because the entry window starts at 9:30 this filter is effectively a guard
  against accidentally entering on pre-9:30 data).

Stop Loss
──────────
ATR-based  : entry_candle_low  − 1.5 × ATR(14)   (long)
             entry_candle_high + 1.5 × ATR(14)   (short)
ORB-based  : ORB midpoint
Use whichever stop is tighter (smaller risk distance from entry).

Target
───────
• Minimum 1.5 × risk (1.5R).
• Once 1R is achieved, switch to trailing MA9 (exit on close < MA9 for long,
  close > MA9 for short).

Exit rules
───────────
• Stop hit.
• Target hit (or MA9 trail after 1R).
• Close below MA21 (long) / above MA21 (short) — trend break.
• 3:15 PM time exit (MIS, no overnight).
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, time as dt_time

from strategy.base import Action, Signal, Strategy

_ORB_START  = dt_time(9, 15)
_ORB_END    = dt_time(9, 30)   # ORB window: 9:15–9:29 candles inclusive
_ENTRY_END  = dt_time(9, 45)   # last allowed entry candle
_TIME_EXIT  = dt_time(15, 15)  # hard time exit


class _SymbolState:
    def __init__(self, atr_period: int, ma_slow: int, volume_lookback: int):
        # ATR (rolling across days, not reset daily)
        self.true_ranges: deque[float] = deque(maxlen=atr_period)
        self.prev_close: float | None = None
        # ORB
        self.orb_high: float | None = None
        self.orb_low: float | None = None
        self.orb_range_pct: float | None = None
        self.orb_ok: bool = False   # range passes 0.3%–1.5% filter
        self.orb_midpoint: float | None = None
        # MA
        self.closes: deque[float] = deque(maxlen=ma_slow)
        # VWAP (reset daily)
        self.cum_pv: float = 0.0
        self.cum_vol: float = 0.0
        # Volume lookback
        self.recent_volumes: deque[float] = deque(maxlen=volume_lookback)
        # Position
        self.position: str | None = None   # "LONG" | "SHORT"
        self.entry_price: float | None = None
        self.entry_candle_high: float | None = None
        self.entry_candle_low: float | None = None
        self.stop_price: float | None = None
        self.target_price: float | None = None
        self.risk: float | None = None   # 1R
        self.trailing: bool = False      # MA9 trail active
        self.time_exited: bool = False   # 3:15 exit already fired today
        # Day tracking
        self.last_day: str | None = None


class ORBTrendStrategy(Strategy):
    name = "orb_trend"

    def __init__(
        self,
        atr_period: int = 14,
        atr_multiplier: float = 1.5,
        ma_fast: int = 9,
        ma_slow: int = 21,
        volume_lookback: int = 5,
        volume_multiplier: float = 1.5,
        orb_min_range_pct: float = 0.3,
        orb_max_range_pct: float = 1.5,
        target_r: float = 1.5,
    ):
        self.atr_period      = atr_period
        self.atr_multiplier  = atr_multiplier
        self.ma_fast         = ma_fast
        self.ma_slow         = ma_slow
        self.volume_lookback = volume_lookback
        self.volume_mult     = volume_multiplier
        self.orb_min_pct     = orb_min_range_pct
        self.orb_max_pct     = orb_max_range_pct
        self.target_r        = target_r
        self._state: dict[str, _SymbolState] = {}

    def _state_for(self, symbol: str) -> _SymbolState:
        return self._state.setdefault(
            symbol, _SymbolState(self.atr_period, self.ma_slow, self.volume_lookback)
        )

    @staticmethod
    def _day(ts) -> str:
        return ts.date().isoformat() if isinstance(ts, datetime) else str(ts)[:10]

    @staticmethod
    def _time(ts) -> dt_time | None:
        return ts.time().replace(second=0, microsecond=0) if isinstance(ts, datetime) else None

    def _atr(self, state: _SymbolState) -> float | None:
        if len(state.true_ranges) < self.atr_period:
            return None
        return sum(state.true_ranges) / self.atr_period

    def _mas(self, state: _SymbolState) -> tuple[float | None, float | None]:
        if len(state.closes) < self.ma_slow:
            return None, None
        closes = list(state.closes)
        ma_slow = sum(closes) / self.ma_slow
        ma_fast = sum(closes[-self.ma_fast:]) / self.ma_fast
        return ma_fast, ma_slow

    def on_candle(self, symbol: str, candle: dict) -> Signal | None:
        state = self._state_for(symbol)
        close  = candle["close"]
        high   = candle["high"]
        low    = candle["low"]
        volume = candle["volume"]
        ts     = candle["date"]
        day    = self._day(ts)
        t      = self._time(ts)

        # ── Day reset ────────────────────────────────────────────────────────
        if day != state.last_day:
            state.orb_high      = None
            state.orb_low       = None
            state.orb_ok        = False
            state.orb_midpoint  = None
            state.orb_range_pct = None
            state.cum_pv        = 0.0
            state.cum_vol       = 0.0
            state.position      = None
            state.entry_price   = state.stop_price = state.target_price = None
            state.risk          = None
            state.trailing      = False
            state.time_exited   = False
            state.last_day      = day
            # Note: prev_close, true_ranges, closes, recent_volumes are NOT
            # reset — they accumulate across days for stable ATR and MA.

        # ── Update indicators ────────────────────────────────────────────────
        typical = (high + low + close) / 3
        state.cum_pv  += typical * volume
        state.cum_vol += volume
        vwap = state.cum_pv / state.cum_vol if state.cum_vol > 0 else close

        # True range (needs prev_close; skip first candle ever).
        if state.prev_close is not None:
            tr = max(high - low, abs(high - state.prev_close), abs(low - state.prev_close))
            state.true_ranges.append(tr)
        state.prev_close = close

        state.closes.append(close)
        state.recent_volumes.append(volume)

        ma_fast, ma_slow = self._mas(state)

        # ── ORB accumulation (9:15–9:29 inclusive) ──────────────────────────
        if t is not None and _ORB_START <= t < _ORB_END:
            state.orb_high = max(state.orb_high, high) if state.orb_high else high
            state.orb_low  = min(state.orb_low,  low)  if state.orb_low  else low

        # ── Finalise ORB at 9:30 ─────────────────────────────────────────────
        if t == _ORB_END and state.orb_high is not None and not state.orb_ok:
            orb_range     = state.orb_high - state.orb_low
            range_pct     = orb_range / state.orb_low * 100
            state.orb_range_pct = range_pct
            state.orb_midpoint  = (state.orb_high + state.orb_low) / 2
            state.orb_ok = self.orb_min_pct <= range_pct <= self.orb_max_pct

        if state.time_exited or t is None:
            return None

        # ── 3:15 time exit ────────────────────────────────────────────────────
        if t >= _TIME_EXIT:
            if state.position is not None:
                state.time_exited = True
                return self._close(state, close, "3:15 PM time exit", ts)
            return None

        # ── Manage open position ─────────────────────────────────────────────
        if state.position is not None:
            return self._manage(state, close, ma_fast, ma_slow, ts)

        # ── Entry check (9:30–9:45) ──────────────────────────────────────────
        if (
            t is not None
            and _ORB_END <= t <= _ENTRY_END
            and state.orb_ok
            and state.orb_high is not None
            and ma_fast is not None
            and ma_slow is not None
        ):
            atr = self._atr(state)
            vol_ok = (
                len(state.recent_volumes) >= 2
                and sum(list(state.recent_volumes)[:-1]) > 0
                and volume >= (sum(list(state.recent_volumes)[:-1]) /
                               (len(state.recent_volumes) - 1)) * self.volume_mult
            )

            # Long entry
            if close > state.orb_high and ma_fast > ma_slow and close > vwap and vol_ok:
                return self._enter(state, "LONG", close, high, low, atr, ts)

            # Short entry
            if close < state.orb_low and ma_fast < ma_slow and close < vwap and vol_ok:
                return self._enter(state, "SHORT", close, high, low, atr, ts)

        return None

    def _enter(
        self,
        state: _SymbolState,
        direction: str,
        close: float,
        high: float,
        low: float,
        atr: float | None,
        ts,
    ) -> Signal:
        midpoint = state.orb_midpoint

        if direction == "LONG":
            atr_stop  = low - self.atr_multiplier * atr if atr else None
            orb_stop  = midpoint
            stop = max(
                atr_stop if atr_stop is not None else orb_stop,
                orb_stop,
            )
        else:
            atr_stop  = high + self.atr_multiplier * atr if atr else None
            orb_stop  = midpoint
            stop = min(
                atr_stop if atr_stop is not None else orb_stop,
                orb_stop,
            )

        risk   = abs(close - stop)
        target = close + self.target_r * risk if direction == "LONG" else close - self.target_r * risk

        state.position         = direction
        state.entry_price      = close
        state.entry_candle_high = high
        state.entry_candle_low  = low
        state.stop_price       = stop
        state.target_price     = target
        state.risk             = risk
        state.trailing         = False

        orb_info = (f"ORB={state.orb_high:.2f}/{state.orb_low:.2f}"
                    f"({state.orb_range_pct:.2f}%) stop={stop:.2f} tgt={target:.2f}")
        return Signal(
            self.name, state.last_day, Action.BUY if direction == "LONG" else Action.SELL,
            close, reason=f"{direction} breakout: {orb_info}", stop_price=stop, ts=ts,
        )

    def _manage(
        self,
        state: _SymbolState,
        close: float,
        ma_fast: float | None,
        ma_slow: float | None,
        ts,
    ) -> Signal | None:
        direction = state.position

        if direction == "LONG":
            # Stop hit.
            if close <= state.stop_price:
                return self._close(state, close, "stop loss hit", ts)
            # Target checked before activating trailing so a candle that
            # simultaneously reaches 1R and the 1.5R target exits cleanly.
            if not state.trailing and close >= state.target_price:
                return self._close(state, close, f"profit target hit ({self.target_r}R)", ts)
            # Activate trailing once 1R profit is reached.
            if state.risk and not state.trailing and close >= state.entry_price + state.risk:
                state.trailing = True
            # MA9 trail (only after trailing activated).
            if state.trailing and ma_fast is not None and close < ma_fast:
                return self._close(state, close, "MA9 trail exit (1R+ achieved)", ts)
            # MA21 trend break.
            if ma_slow is not None and close < ma_slow:
                return self._close(state, close, "close below MA21 (trend break)", ts)

        else:  # SHORT
            if close >= state.stop_price:
                return self._close(state, close, "stop loss hit", ts)
            if not state.trailing and close <= state.target_price:
                return self._close(state, close, f"profit target hit ({self.target_r}R)", ts)
            if state.risk and not state.trailing and close <= state.entry_price - state.risk:
                state.trailing = True
            if state.trailing and ma_fast is not None and close > ma_fast:
                return self._close(state, close, "MA9 trail exit (1R+ achieved)", ts)
            if ma_slow is not None and close > ma_slow:
                return self._close(state, close, "close above MA21 (trend break)", ts)

        return None

    def _close(self, state: _SymbolState, price: float, reason: str, ts) -> Signal:
        direction = state.position
        state.position = state.entry_price = state.stop_price = None
        state.target_price = state.risk = None
        state.trailing = False
        return Signal(
            self.name, state.last_day, Action.EXIT, price, reason=reason, ts=ts,
        )

    def force_exit(self, symbol: str, price: float, ts) -> Signal | None:
        state = self._state.get(symbol)
        if state and state.position:
            return self._close(state, price, "EOD square-off", ts)
        return None
