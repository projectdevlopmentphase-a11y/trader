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
        if self.mode in ("live",) and not self.kite_api_key:
            raise ValueError("KITE_API_KEY is required for live mode")


settings = Settings()
