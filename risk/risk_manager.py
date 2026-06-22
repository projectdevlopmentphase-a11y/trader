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
        n_pairs: int = 1,
    ):
        self.capital = capital
        self.risk_pct_per_trade = risk_pct_per_trade
        self.daily_loss_cap_pct = daily_loss_cap_pct
        self.max_consecutive_errors = max_consecutive_errors
        # Each pair independently targets pairs_capital_pct of capital, so
        # with n_pairs configured pairs that could oversubscribe capital by
        # up to n_pairs x if they're all open at once -- split evenly across
        # the configured pairs up front instead.
        self.pairs_capital_pct = pairs_capital_pct / max(1, n_pairs)
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

    def equal_weight_position_size(self, price: float, capital_pct: float, n_positions: int) -> int:
        """Shares for one of n_positions equal-weight slots, each sized at
        capital_pct of capital split evenly across the slots."""
        if price <= 0 or n_positions <= 0:
            return 0
        notional = self.capital * (capital_pct / 100) / n_positions
        return max(0, int(notional / price))

    def swing_position_size(
        self, entry_price: float, stop_loss_price: float, min_stop_distance_pct: float = 2.0
    ) -> int:
        """Same risk-based sizing as position_size, but floors the stop
        distance at min_stop_distance_pct of entry price -- a tight intraday
        stop on a multi-day swing hold would otherwise oversize the position
        relative to the overnight gap risk it's actually exposed to."""
        if entry_price <= 0:
            return 0
        risk_amount = self.capital * (self.risk_pct_per_trade / 100)
        stop_distance = max(abs(entry_price - stop_loss_price), entry_price * min_stop_distance_pct / 100)
        if stop_distance <= 0:
            return 0
        return max(0, int(risk_amount / stop_distance))

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
