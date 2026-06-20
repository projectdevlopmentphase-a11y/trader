"""Zerodha MIS (intraday equity) transaction cost model.

Single source of truth for cost/slippage assumptions, shared by every
executor so backtest, paper and live reporting are never comparing
gross numbers from one mode against net numbers from another.

Rates follow Zerodha's published intraday-equity charge schedule.
"""
from __future__ import annotations

_BROKERAGE_RATE = 0.0003  # 0.03%
_BROKERAGE_CAP = 20.0  # Rs 20/order, whichever is lower
_STT_RATE = 0.00025  # 0.025%, sell side only
_EXCHANGE_TXN_RATE = 0.0000297  # NSE
_SEBI_RATE = 10 / 1e7  # Rs 10 per crore of turnover
_STAMP_DUTY_RATE = 0.00003  # 0.003%, buy side only
_GST_RATE = 0.18  # on brokerage + exchange txn charges + SEBI charges


def calculate_transaction_cost(side: str, price: float, quantity: int) -> float:
    """Total statutory + brokerage cost for one executed leg (one side of
    one fill), in rupees."""
    turnover = price * quantity
    brokerage = min(_BROKERAGE_RATE * turnover, _BROKERAGE_CAP)
    stt = _STT_RATE * turnover if side == "SELL" else 0.0
    exchange_txn = _EXCHANGE_TXN_RATE * turnover
    sebi = _SEBI_RATE * turnover
    stamp_duty = _STAMP_DUTY_RATE * turnover if side == "BUY" else 0.0
    gst = _GST_RATE * (brokerage + exchange_txn + sebi)
    return brokerage + stt + exchange_txn + sebi + stamp_duty + gst


def apply_slippage(side: str, price: float, slippage_bps: float) -> float:
    """Adjusts a signal price unfavorably to approximate a realistic fill:
    buys fill higher, sells fill lower, by slippage_bps basis points."""
    adjustment = price * (slippage_bps / 10_000)
    return price + adjustment if side == "BUY" else price - adjustment
