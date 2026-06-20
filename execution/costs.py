"""Zerodha transaction cost models: intraday (MIS) and delivery (CNC).

Single source of truth for cost/slippage assumptions, shared by every
executor so backtest, paper and live reporting are never comparing
gross numbers from one mode against net numbers from another.

Rates follow Zerodha's published charge schedules.
"""
from __future__ import annotations

_BROKERAGE_RATE = 0.0003  # 0.03%
_BROKERAGE_CAP = 20.0  # Rs 20/order, whichever is lower
_STT_RATE = 0.00025  # 0.025%, sell side only (intraday)
_EXCHANGE_TXN_RATE = 0.0000297  # NSE, both products
_SEBI_RATE = 10 / 1e7  # Rs 10 per crore of turnover, both products
_STAMP_DUTY_RATE = 0.00003  # 0.003%, buy side only (intraday)
_GST_RATE = 0.18  # on brokerage + exchange txn charges + SEBI charges

# Delivery (CNC) has no brokerage at Zerodha, but a higher STT (charged on
# both legs, not just the sell) and higher stamp duty than intraday.
_DELIVERY_STT_RATE = 0.001  # 0.1%, both buy and sell
_DELIVERY_STAMP_DUTY_RATE = 0.00015  # 0.015%, buy side only


def calculate_transaction_cost(side: str, price: float, quantity: int, product_type: str = "MIS") -> float:
    """Total statutory + brokerage cost for one executed leg (one side of
    one fill), in rupees. product_type is "MIS" (intraday, default) or
    "CNC" (delivery/swing)."""
    if product_type == "CNC":
        return _delivery_transaction_cost(side, price, quantity)
    return _intraday_transaction_cost(side, price, quantity)


def _intraday_transaction_cost(side: str, price: float, quantity: int) -> float:
    turnover = price * quantity
    brokerage = min(_BROKERAGE_RATE * turnover, _BROKERAGE_CAP)
    stt = _STT_RATE * turnover if side == "SELL" else 0.0
    exchange_txn = _EXCHANGE_TXN_RATE * turnover
    sebi = _SEBI_RATE * turnover
    stamp_duty = _STAMP_DUTY_RATE * turnover if side == "BUY" else 0.0
    gst = _GST_RATE * (brokerage + exchange_txn + sebi)
    return brokerage + stt + exchange_txn + sebi + stamp_duty + gst


def _delivery_transaction_cost(side: str, price: float, quantity: int) -> float:
    turnover = price * quantity
    brokerage = 0.0
    stt = _DELIVERY_STT_RATE * turnover  # both sides, unlike intraday
    exchange_txn = _EXCHANGE_TXN_RATE * turnover
    sebi = _SEBI_RATE * turnover
    stamp_duty = _DELIVERY_STAMP_DUTY_RATE * turnover if side == "BUY" else 0.0
    gst = _GST_RATE * (brokerage + exchange_txn + sebi)
    return brokerage + stt + exchange_txn + sebi + stamp_duty + gst


def apply_slippage(side: str, price: float, slippage_bps: float) -> float:
    """Adjusts a signal price unfavorably to approximate a realistic fill:
    buys fill higher, sells fill lower, by slippage_bps basis points."""
    adjustment = price * (slippage_bps / 10_000)
    return price + adjustment if side == "BUY" else price - adjustment
