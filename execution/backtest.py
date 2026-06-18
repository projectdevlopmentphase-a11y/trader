"""Simulates fills at the signal's candle close price. No API calls."""
from __future__ import annotations

from execution.base import Fill, OrderExecutor
from storage.db import log_trade
from strategy.base import Action, Signal


class BacktestExecutor(OrderExecutor):
    mode = "backtest"

    def execute(self, signal: Signal, quantity: int) -> Fill:
        side = "BUY" if signal.action == Action.BUY else "SELL"
        fill = Fill(
            symbol=signal.symbol,
            side=side,
            quantity=quantity,
            price=signal.price,
            order_id=None,
            status="FILLED",
        )
        log_trade(self.mode, signal.strategy, signal.symbol, side, quantity, signal.price, "FILLED", ts=signal.ts)
        return fill
