"""Simulates fills on live ticks/candles without placing real orders.

Same behavior as backtest (fill at signal price) but logged under mode
"paper" so trade history is clearly separated from real fills.
"""
from __future__ import annotations

from execution.base import Fill, OrderExecutor
from storage.db import log_trade
from strategy.base import Action, Signal


class PaperExecutor(OrderExecutor):
    mode = "paper"

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
        log_trade(self.mode, signal.strategy, signal.symbol, side, quantity, signal.price, "FILLED")
        return fill
