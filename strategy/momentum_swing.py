"""Cross-sectional price momentum, rebalanced monthly.

Standard momentum factor construction: ranks symbols by their trailing
return over `lookback_months`, excluding the most recent `skip_months` (the
skip avoids short-term mean-reversion contaminating the momentum signal).
Holds the top `top_n` symbols, equal-weighted, until the next rebalance.

Most entries/exits come out of compute_rebalance, called once a month by the
caller (see main.py's run_backtest_swing / run_swing_live_or_paper), since
this strategy's logic is inherently cross-sectional rather than a single
symbol's per-candle reaction. on_candle additionally checks each held
position against its stop_pct on every close -- without this, a position
that gaps far past its stop early in the month would otherwise sit
unguarded until the next rebalance, up to a month later. A symbol that gets
stopped out is excluded from re-entry for stop_cooldown_months afterward,
since the 12-month trailing-return signal barely moves month to month and
would otherwise often rank the same symbol straight back into the top-N the
very next rebalance, whipsawing in and out of the same loser.
"""
from __future__ import annotations

from collections import deque
from datetime import date, datetime

from strategy.base import Action, Signal, Strategy

_TRADING_DAYS_PER_MONTH = 22


def _month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _add_months(year: int, month: int, n: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + n
    return total // 12, total % 12 + 1


class _SymbolState:
    def __init__(self, maxlen: int):
        self.closes: deque[float] = deque(maxlen=maxlen)


class MomentumSwingStrategy(Strategy):
    name = "momentum_swing"

    def __init__(
        self,
        lookback_months: int = 12,
        skip_months: int = 1,
        top_n: int = 5,
        rebalance_day_of_month: int = 1,
        stop_pct: float = 10.0,
        stop_cooldown_months: int = 1,
    ):
        self.lookback_months = lookback_months
        self.skip_months = skip_months
        self.top_n = top_n
        self.rebalance_day_of_month = rebalance_day_of_month
        self.stop_pct = stop_pct
        self.stop_cooldown_months = stop_cooldown_months
        self._maxlen = lookback_months * _TRADING_DAYS_PER_MONTH
        self._state: dict[str, _SymbolState] = {}
        self._held: set[str] = set()
        self._stop_price: dict[str, float] = {}
        # symbol -> earliest month_key ("YYYY-MM") it's eligible for re-entry.
        self._cooldown_until: dict[str, str] = {}
        self._last_rebalanced_month: str | None = None

    def _state_for(self, symbol: str) -> _SymbolState:
        return self._state.setdefault(symbol, _SymbolState(self._maxlen))

    def on_candle(self, symbol: str, candle: dict) -> Signal | None:
        close = candle["close"]
        self._state_for(symbol).closes.append(close)

        stop_price = self._stop_price.get(symbol)
        if stop_price is not None and close <= stop_price:
            self._held.discard(symbol)
            del self._stop_price[symbol]
            candle_date = candle["date"]
            stopped_day = candle_date.date() if isinstance(candle_date, datetime) else candle_date
            # +1 so the cooldown actually blocks the next stop_cooldown_months
            # rebalances rather than expiring right at the first one.
            cooldown_year, cooldown_month = _add_months(stopped_day.year, stopped_day.month, self.stop_cooldown_months + 1)
            self._cooldown_until[symbol] = _month_key(cooldown_year, cooldown_month)
            return Signal(
                self.name, symbol, Action.EXIT, close,
                reason=f"stop loss hit ({self.stop_pct:.1f}%)", ts=candle["date"],
            )
        return None

    def _trailing_return(self, closes: deque[float]) -> float | None:
        skip_days = self.skip_months * _TRADING_DAYS_PER_MONTH
        lookback_days = self.lookback_months * _TRADING_DAYS_PER_MONTH
        if len(closes) < lookback_days:
            return None
        if skip_days >= lookback_days:
            return None
        history = list(closes)
        start_price = history[-lookback_days]
        end_price = history[-skip_days - 1] if skip_days > 0 else history[-1]
        if start_price <= 0:
            return None
        return (end_price - start_price) / start_price

    def compute_rebalance(self, current_date: date | datetime) -> list[Signal]:
        day = current_date.date() if isinstance(current_date, datetime) else current_date
        month_key = f"{day.year:04d}-{day.month:02d}"
        if day.day < self.rebalance_day_of_month:
            return []
        if self._last_rebalanced_month == month_key:
            return []
        self._last_rebalanced_month = month_key

        returns: list[tuple[str, float]] = []
        for symbol, state in self._state.items():
            ret = self._trailing_return(state.closes)
            if ret is not None:
                returns.append((symbol, ret))
        returns.sort(key=lambda item: item[1], reverse=True)
        # A symbol still in cooldown is skipped as a candidate so the next-
        # ranked eligible symbol fills its slot instead.
        eligible = [
            (symbol, ret) for symbol, ret in returns
            if self._cooldown_until.get(symbol, month_key) <= month_key
        ]
        top_symbols = {symbol for symbol, _ in eligible[: self.top_n]}
        prices = {symbol: state.closes[-1] for symbol, state in self._state.items() if state.closes}

        signals: list[Signal] = []
        for symbol in top_symbols - self._held:
            price = prices.get(symbol)
            if price is None:
                continue
            stop_price = price * (1 - self.stop_pct / 100)
            self._stop_price[symbol] = stop_price
            signals.append(
                Signal(
                    self.name, symbol, Action.BUY, price,
                    reason="entered momentum top-N", stop_price=stop_price, ts=current_date,
                )
            )
        for symbol in self._held - top_symbols:
            price = prices.get(symbol)
            if price is None:
                continue
            self._stop_price.pop(symbol, None)
            signals.append(
                Signal(self.name, symbol, Action.EXIT, price, reason="fell out of momentum top-N", ts=current_date)
            )

        self._held = top_symbols
        return signals
