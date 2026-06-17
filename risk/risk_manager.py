"""Position sizing, daily loss cap, and an error-based kill switch.

This sits between the strategy engine and the order executor: every signal
must pass through `approve_signal` before an order is placed, and every
fill's P&L must go through `record_pnl` so the daily loss cap stays accurate.
"""
from __future__ import annotations

from datetime import date

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
    ):
        self.capital = capital
        self.risk_pct_per_trade = risk_pct_per_trade
        self.daily_loss_cap_pct = daily_loss_cap_pct
        self.max_consecutive_errors = max_consecutive_errors
        self._consecutive_errors = 0
        self._killed = False

    def _today(self) -> str:
        return date.today().isoformat()

    def daily_loss_cap_breached(self) -> bool:
        today = self._today()
        if is_halted(today):
            return True
        loss_cap = self.capital * (self.daily_loss_cap_pct / 100)
        pnl = get_daily_pnl(today)
        if pnl <= -loss_cap:
            set_halted(today, True)
            return True
        return False

    def position_size(self, entry_price: float, stop_loss_price: float) -> int:
        """Shares to buy/sell so a stop-out loses at most risk_pct_per_trade of capital."""
        risk_amount = self.capital * (self.risk_pct_per_trade / 100)
        per_share_risk = abs(entry_price - stop_loss_price)
        if per_share_risk <= 0:
            return 0
        return max(0, int(risk_amount / per_share_risk))

    def approve_signal(self) -> bool:
        """Returns False if trading should be blocked right now (loss cap or kill switch)."""
        if self._killed:
            return False
        if self.daily_loss_cap_breached():
            return False
        return True

    def record_pnl(self, pnl: float) -> float:
        return update_daily_pnl(self._today(), pnl)

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
