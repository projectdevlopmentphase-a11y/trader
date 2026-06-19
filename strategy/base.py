"""Common interface every strategy (ORB, VWAP, EMA crossover, Supertrend, ...)
must implement, so the rest of the system (risk manager, order executor)
never needs to know which strategy produced a signal.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    EXIT = "EXIT"


@dataclass
class Signal:
    strategy: str
    symbol: str
    action: Action
    price: float
    reason: str = ""
    stop_price: float | None = None
    # The candle's own date/time that triggered this signal (market time),
    # not wall-clock time -- crucial in backtest where they're unrelated.
    ts: object = None
    # Pairs-trading legs: when pair_id is set, this signal represents a single
    # two-leg position (this symbol is leg 1) that must be opened/closed as
    # one unit -- see TraderApp._handle_pair_signal in main.py.
    pair_id: str | None = None
    leg2_symbol: str | None = None
    leg2_action: Action | None = None
    leg2_price: float | None = None
    hedge_ratio: float | None = None


class Strategy(ABC):
    """One instance tracks state for all symbols in the watchlist."""

    name: str = "base"

    @abstractmethod
    def on_candle(self, symbol: str, candle: dict) -> Signal | None:
        """candle is a dict with keys: date, open, high, low, close, volume.

        Returns a Signal if this candle triggers an entry/exit, else None.
        """
        raise NotImplementedError
