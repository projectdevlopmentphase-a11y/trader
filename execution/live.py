"""Places real Kite orders. MIS intraday product, market orders with a
non-zero market_protection (mandatory under the SEBI framework or the
order is rejected).
"""
from __future__ import annotations

from kiteconnect import KiteConnect

from config.settings import settings
from execution.base import Fill, OrderExecutor
from execution.costs import calculate_transaction_cost
from storage.db import log_error, log_trade
from strategy.base import Action, Signal


class LiveExecutor(OrderExecutor):
    mode = "live"

    def __init__(self, kite: KiteConnect, market_protection_pct: float | None = None):
        self.kite = kite
        self.market_protection_pct = market_protection_pct or settings.market_protection_pct

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
                product=self.kite.PRODUCT_MIS,
                order_type=self.kite.ORDER_TYPE_MARKET,
                market_protection=self.market_protection_pct,
            )
            status = "PLACED"
        except Exception as exc:
            log_error("live_executor", str(exc))
            order_id = None
            status = "FAILED"

        cost = calculate_transaction_cost(side, signal.price, quantity)
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
            order_id=order_id, cost=cost, ts=signal.ts,
        )
        return fill
