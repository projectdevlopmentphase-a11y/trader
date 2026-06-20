"""Places real Kite orders for swing (CNC delivery) strategies. CNC market
orders don't require market_protection (that's an MIS-only SEBI requirement).
"""
from __future__ import annotations

from kiteconnect import KiteConnect

from execution.base import Fill, OrderExecutor
from execution.costs import calculate_transaction_cost
from storage.db import log_error, log_trade
from strategy.base import Action, Signal


class SwingExecutor(OrderExecutor):
    mode = "live"

    def __init__(self, kite: KiteConnect):
        self.kite = kite

    def execute(self, signal: Signal, quantity: int) -> Fill:
        side = "BUY" if signal.action == Action.BUY else "SELL"
        transaction_type = self.kite.TRANSACTION_TYPE_BUY if side == "BUY" else self.kite.TRANSACTION_TYPE_SELL

        try:
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=self.kite.EXCHANGE_NSE,
                tradingsymbol=signal.symbol,
                transaction_type=transaction_type,
                quantity=quantity,
                product=self.kite.PRODUCT_CNC,
                order_type=self.kite.ORDER_TYPE_MARKET,
            )
            status = "PLACED"
        except Exception as exc:
            log_error("swing_executor", str(exc))
            order_id = None
            status = "FAILED"

        cost = calculate_transaction_cost(side, signal.price, quantity, product_type="CNC")
        fill = Fill(
            symbol=signal.symbol,
            side=side,
            quantity=quantity,
            price=signal.price,
            order_id=order_id,
            status=status,
            cost=cost,
        )
        log_trade(
            self.mode, signal.strategy, signal.symbol, side, quantity, signal.price, status,
            order_id=order_id, cost=cost, pair_id=signal.pair_id, ts=signal.ts,
        )
        return fill
