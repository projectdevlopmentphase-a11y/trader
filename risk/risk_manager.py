"""Position sizing, daily loss cap, and an error-based kill switch.

This sits between the strategy engine and the order executor: every signal
must pass through `approve_signal` before an order is placed, and every
fill's P&L must go through `record_pnl` so the daily loss cap stays accurate.
"""
from __future__ import annotations

from storage.db import get_daily_pnl, is_halted, log_error, set_halted, update_daily_pnl


class KillSwitchTripped(Exception):
    pass


class RiskManager:
    def __init__(
        self,
        capital: float,
        risk_pct_per_trade: float,
        daily_loss_cap_pct: float,
        max_consecutive_errors: int,
        pairs_capital_pct: float = 10.0,
    ):
        self.capital = capital
        self.risk_pct_per_trade = risk_pct_per_trade
        self.daily_loss_cap_pct = daily_loss_cap_pct
        self.max_consecutive_errors = max_consecutive_errors
        self.pairs_capital_pct = pairs_capital_pct
        self._consecutive_errors = 0
        self._killed = False

    def daily_loss_cap_breached(self, trade_date: str) -> bool:
        if is_halted(trade_date):
            return True
        loss_cap = self.capital * (self.daily_loss_cap_pct / 100)
        pnl = get_daily_pnl(trade_date)
        if pnl <= -loss_cap:
            set_halted(trade_date, True)
            return True
        return False

    def position_size(self, entry_price: float, stop_loss_price: float) -> int:
        """Shares to buy/sell so a stop-out loses at most risk_pct_per_trade of capital."""
        risk_amount = self.capital * (self.risk_pct_per_trade / 100)
        per_share_risk = abs(entry_price - stop_loss_price)
        if per_share_risk <= 0:
            return 0
        return max(0, int(risk_amount / per_share_risk))

    def pairs_position_size(self, price_a: float, price_b: float, hedge_ratio: float) -> tuple[int, int]:
        """Shares for both legs of a pairs trade. Pairs have no stop-loss
        price to size off (they exit on mean reversion, not a price stop),
        so sizing here is notional-based: pairs_capital_pct of capital is the
        target combined exposure across both legs, split so the position
        stays beta-neutral per hedge_ratio (qty_b = qty_a * hedge_ratio)."""
        if price_a <= 0 or price_b <= 0:
            return 0, 0
        total_notional = self.capital * (self.pairs_capital_pct / 100)
        qty_a = int(total_notional / (price_a + hedge_ratio * price_b))
        qty_b = int(qty_a * hedge_ratio)
        return max(0, qty_a), max(0, qty_b)

    def approve_signal(self, trade_date: str) -> bool:
        """Returns False if trading should be blocked right now (loss cap or kill switch)."""
        if self._killed:
            return False
        if self.daily_loss_cap_breached(trade_date):
            return False
        return True

    def record_pnl(self, trade_date: str, pnl: float) -> float:
        return update_daily_pnl(trade_date, pnl)

    def record_error(self, source: str, message: str) -> None:
        log_error(source, message)
        self._consecutive_errors += 1
        if self._consecutive_errors >= self.max_consecutive_errors:
            self._killed = True
            raise KillSwitchTripped(
                f"{self._consecutive_errors} consecutive errors; kill switch tripped"
            )

    def record_success(self) -> None:
        self._consecutive_errors = 0

    @property
    def is_killed(self) -> bool:
        return self._killed
