"""Simulates fills at the signal's candle close price. No API calls."""
from __future__ import annotations

from config.settings import settings
from execution.base import Fill, OrderExecutor
from execution.costs import apply_slippage, calculate_transaction_cost
from storage.db import log_trade
from strategy.base import Action, Signal


class BacktestExecutor(OrderExecutor):
    mode = "backtest"

    def execute(self, signal: Signal, quantity: int) -> Fill:
        side = "BUY" if signal.action == Action.BUY else "SELL"
        fill_price = apply_slippage(side, signal.price, settings.slippage_bps)
        cost = calculate_transaction_cost(side, fill_price, quantity)
        fill = Fill(
            symbol=signal.symbol,
            side=side,
            quantity=quantity,
            price=fill_price,
            order_id=None,
            status="FILLED",
            cost=cost,
        )
        log_trade(
            self.mode, signal.strategy, signal.symbol, side, quantity, fill_price, "FILLED",
            cost=cost, pair_id=signal.pair_id, ts=signal.ts,
        )
        return fill
