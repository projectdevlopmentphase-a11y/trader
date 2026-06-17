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

    orb_range_minutes: int = field(
        default_factory=lambda: int(os.getenv("ORB_RANGE_MINUTES", "15"))
    )
    orb_candle_interval: str = field(
        default_factory=lambda: os.getenv("ORB_CANDLE_INTERVAL", "5minute")
    )

    market_protection_pct: float = field(
        default_factory=lambda: float(os.getenv("MARKET_PROTECTION_PCT", "1.0"))
    )
    square_off_time: str = field(default_factory=lambda: os.getenv("SQUARE_OFF_TIME", "15:20"))

    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "trader.db"))

    def __post_init__(self) -> None:
        if self.mode not in VALID_MODES:
            raise ValueError(f"MODE must be one of {VALID_MODES}, got {self.mode!r}")
        if self.mode in ("live",) and not self.kite_api_key:
            raise ValueError("KITE_API_KEY is required for live mode")


settings = Settings()
