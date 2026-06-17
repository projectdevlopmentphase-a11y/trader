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


class Strategy(ABC):
    """One instance tracks state for all symbols in the watchlist."""

    name: str = "base"

    @abstractmethod
    def on_candle(self, symbol: str, candle: dict) -> Signal | None:
        """candle is a dict with keys: date, open, high, low, close, volume.

        Returns a Signal if this candle triggers an entry/exit, else None.
        """
        raise NotImplementedError
