"""Mode-aware order executor interface.

Backtest, paper and live executors all implement `execute(signal, quantity)`
with the same signature and return shape, so strategy/risk code never has to
branch on mode.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from strategy.base import Signal


@dataclass
class Fill:
    symbol: str
    side: str
    quantity: int
    price: float
    order_id: str | None
    status: str
    cost: float = 0.0


class OrderExecutor(ABC):
    mode: str = "base"

    @abstractmethod
    def execute(self, signal: Signal, quantity: int) -> Fill:
        raise NotImplementedError
