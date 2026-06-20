"""Loads configuration and secrets from the environment / .env file.

All other modules should import `settings` from here rather than calling
os.environ / load_dotenv themselves, so there is a single source of truth.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _get_list(name: str, default: str = "") -> list[str]:
    val = os.getenv(name, default)
    return [item.strip() for item in val.split(",") if item.strip()]


VALID_MODES = ("backtest", "paper", "live")
VALID_STRATEGIES = ("orb", "momentum_halfhour", "pairs", "momentum_swing", "breakout_swing")


@dataclass
class Settings:
    kite_api_key: str = field(default_factory=lambda: os.getenv("KITE_API_KEY", ""))
    kite_api_secret: str = field(default_factory=lambda: os.getenv("KITE_API_SECRET", ""))
    kite_access_token: str = field(default_factory=lambda: os.getenv("KITE_ACCESS_TOKEN", ""))

    mode: str = field(default_factory=lambda: os.getenv("MODE", "paper"))

    capital: float = field(default_factory=lambda: float(os.getenv("CAPITAL", "100000")))
    risk_pct_per_trade: float = field(
        default_factory=lambda: float(os.getenv("RISK_PCT_PER_TRADE", "1.0"))
    )
    daily_loss_cap_pct: float = field(
        default_factory=lambda: float(os.getenv("DAILY_LOSS_CAP_PCT", "3.0"))
    )
    max_consecutive_errors: int = field(
        default_factory=lambda: int(os.getenv("MAX_CONSECUTIVE_ERRORS", "5"))
    )

    watchlist: list[str] = field(default_factory=lambda: _get_list("WATCHLIST"))

    # Daily scan: picks the day's trading watchlist from this broader universe
    # based on relative volume / gap activity, instead of always trading the
    # same fixed symbols. Falls back to `watchlist` if left empty.
    scan_universe: list[str] = field(default_factory=lambda: _get_list("SCAN_UNIVERSE"))
    scan_top_n: int = field(default_factory=lambda: int(os.getenv("SCAN_TOP_N", "3")))
    scan_min_relative_volume: float = field(
        default_factory=lambda: float(os.getenv("SCAN_MIN_RELATIVE_VOLUME", "1.5"))
    )
    scan_min_gap_pct: float = field(
        default_factory=lambda: float(os.getenv("SCAN_MIN_GAP_PCT", "0.5"))
    )
    scan_lookback_days: int = field(
        default_factory=lambda: int(os.getenv("SCAN_LOOKBACK_DAYS", "20"))
    )

    backtest_days: int = field(default_factory=lambda: int(os.getenv("BACKTEST_DAYS", "180")))

    # Which strategy to run: "orb" or "momentum_halfhour".
    strategy: str = field(default_factory=lambda: os.getenv("STRATEGY", "orb"))

    orb_range_minutes: int = field(
        default_factory=lambda: int(os.getenv("ORB_RANGE_MINUTES", "15"))
    )
    orb_candle_interval: str = field(
        default_factory=lambda: os.getenv("ORB_CANDLE_INTERVAL", "5minute")
    )
    # Volume confirmation: breakout candle's volume must be >= this multiple
    # of the recent average volume. Set to 0 to disable the filter.
    orb_volume_multiplier: float = field(
        default_factory=lambda: float(os.getenv("ORB_VOLUME_MULTIPLIER", "1.5"))
    )
    orb_volume_lookback: int = field(
        default_factory=lambda: int(os.getenv("ORB_VOLUME_LOOKBACK", "20"))
    )
    # NR7-style filter: only take entries on a day whose prior day's range was
    # the narrowest of the last N trading days. Set to 0 to disable.
    orb_nr7_lookback: int = field(
        default_factory=lambda: int(os.getenv("ORB_NR7_LOOKBACK", "7"))
    )
    # No new entries after this time of day (24h "HH:MM", candle's own clock).
    orb_entry_cutoff_time: str = field(
        default_factory=lambda: os.getenv("ORB_ENTRY_CUTOFF_TIME", "13:00")
    )
    # Profit target as a multiple of the entry's risk (entry-to-stop distance).
    # E.g. 2.0 means exit once unrealized gain reaches 2x the risked amount.
    # Set to 0 to disable (winners only exit via EOD square-off).
    orb_target_r: float = field(default_factory=lambda: float(os.getenv("ORB_TARGET_R", "2.0")))

    # First-half-hour -> last-half-hour momentum strategy.
    momentum_window_minutes: int = field(
        default_factory=lambda: int(os.getenv("MOMENTUM_WINDOW_MINUTES", "30"))
    )
    momentum_volume_multiplier: float = field(
        default_factory=lambda: float(os.getenv("MOMENTUM_VOLUME_MULTIPLIER", "1.5"))
    )
    momentum_volume_lookback: int = field(
        default_factory=lambda: int(os.getenv("MOMENTUM_VOLUME_LOOKBACK", "20"))
    )

    # Pairs trading. Pairs + hedge ratios come from screening/pair_finder.py's
    # output (re-run periodically -- cointegration can break over time).
    pairs_config_path: str = field(
        default_factory=lambda: os.getenv("PAIRS_CONFIG_PATH", "config/pairs.json")
    )
    pairs_spread_lookback: int = field(
        default_factory=lambda: int(os.getenv("PAIRS_SPREAD_LOOKBACK", "20"))
    )
    pairs_entry_z: float = field(default_factory=lambda: float(os.getenv("PAIRS_ENTRY_Z", "2.0")))
    pairs_exit_z: float = field(default_factory=lambda: float(os.getenv("PAIRS_EXIT_Z", "0.5")))
    # Target combined notional exposure for both legs of a pairs trade, as a
    # % of capital. Pairs have no stop-loss price to size off (they exit on
    # mean reversion), so this replaces risk_pct_per_trade for sizing.
    pairs_capital_pct: float = field(
        default_factory=lambda: float(os.getenv("PAIRS_CAPITAL_PCT", "10.0"))
    )

    # Swing trading (CNC delivery, daily candles, multi-day holds).
    swing_universe: list[str] = field(
        default_factory=lambda: _get_list(
            "SWING_UNIVERSE",
            "RELIANCE,TCS,INFY,HDFCBANK,ICICIBANK,SBIN,ITC,LT,AXISBANK,KOTAKBANK",
        )
    )
    swing_capital_pct: float = field(
        default_factory=lambda: float(os.getenv("SWING_CAPITAL_PCT", "30.0"))
    )
    momentum_swing_lookback_months: int = field(
        default_factory=lambda: int(os.getenv("MOMENTUM_SWING_LOOKBACK_MONTHS", "12"))
    )
    momentum_swing_skip_months: int = field(
        default_factory=lambda: int(os.getenv("MOMENTUM_SWING_SKIP_MONTHS", "1"))
    )
    momentum_swing_top_n: int = field(
        default_factory=lambda: int(os.getenv("MOMENTUM_SWING_TOP_N", "5"))
    )
    momentum_swing_rebalance_day_of_month: int = field(
        default_factory=lambda: int(os.getenv("MOMENTUM_SWING_REBALANCE_DAY_OF_MONTH", "1"))
    )
    momentum_swing_stop_pct: float = field(
        default_factory=lambda: float(os.getenv("MOMENTUM_SWING_STOP_PCT", "10.0"))
    )
    breakout_swing_lookback_days: int = field(
        default_factory=lambda: int(os.getenv("BREAKOUT_SWING_LOOKBACK_DAYS", "20"))
    )
    breakout_swing_trailing_stop_days: int = field(
        default_factory=lambda: int(os.getenv("BREAKOUT_SWING_TRAILING_STOP_DAYS", "10"))
    )
    swing_min_stop_distance_pct: float = field(
        default_factory=lambda: float(os.getenv("SWING_MIN_STOP_DISTANCE_PCT", "2.0"))
    )
    swing_daily_run_time: str = field(
        default_factory=lambda: os.getenv("SWING_DAILY_RUN_TIME", "15:45")
    )

    # Flat per-fill slippage assumption (basis points) applied unfavorably in
    # backtest/paper simulated fills: buys fill higher, sells fill lower.
    # Tune this once real paper-trading fills show a more realistic number.
    slippage_bps: float = field(default_factory=lambda: float(os.getenv("SLIPPAGE_BPS", "5")))

    market_protection_pct: float = field(
        default_factory=lambda: float(os.getenv("MARKET_PROTECTION_PCT", "1.0"))
    )
    square_off_time: str = field(default_factory=lambda: os.getenv("SQUARE_OFF_TIME", "15:20"))

    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "trader.db"))

    # Cloudflare D1 sync (dashboard data source) — only needed for the /sync endpoint.
    cf_account_id: str = field(default_factory=lambda: os.getenv("CF_ACCOUNT_ID", ""))
    cf_api_token: str = field(default_factory=lambda: os.getenv("CF_API_TOKEN", ""))
    cf_d1_database_id: str = field(default_factory=lambda: os.getenv("CF_D1_DATABASE_ID", ""))
    sync_api_token: str = field(default_factory=lambda: os.getenv("SYNC_API_TOKEN", ""))
    sync_api_port: int = field(default_factory=lambda: int(os.getenv("SYNC_API_PORT", "8787")))

    def __post_init__(self) -> None:
        if self.mode not in VALID_MODES:
            raise ValueError(f"MODE must be one of {VALID_MODES}, got {self.mode!r}")
        if self.strategy not in VALID_STRATEGIES:
            raise ValueError(f"STRATEGY must be one of {VALID_STRATEGIES}, got {self.strategy!r}")
        if self.mode in ("live",) and not self.kite_api_key:
            raise ValueError("KITE_API_KEY is required for live mode")


settings = Settings()
