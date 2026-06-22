"""Quick probe: how far back does Kite's historical API actually serve daily
candles right now? Requests a wide window and reports the earliest candle
date returned per symbol, so you can confirm the Historical Data add-on is
unlocking more than the ~6-month free-tier ceiling before relying on it for
momentum_swing's 12-month lookback.

Usage:
    python scripts/check_history_depth.py
    python scripts/check_history_depth.py --symbols RELIANCE TCS --years 3
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from auth.kite_auth import KiteAuth  # noqa: E402
from config.settings import settings  # noqa: E402
from data.historical import fetch_historical_candles, fetch_instruments  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=settings.swing_universe[:3])
    parser.add_argument("--years", type=int, default=5, help="How far back to request (worst case)")
    args = parser.parse_args()

    auth = KiteAuth()
    kite = auth.authenticated_client()

    instruments = fetch_instruments(kite, "NSE")
    symbol_to_token = {i["tradingsymbol"]: i["instrument_token"] for i in instruments}

    to_date = datetime.now()
    from_date = to_date - timedelta(days=args.years * 365)

    print(f"Requesting {args.years} years of daily candles ({from_date.date()} -> {to_date.date()})\n")
    for symbol in args.symbols:
        token = symbol_to_token.get(symbol)
        if token is None:
            print(f"  {symbol:<12} unknown symbol")
            continue
        candles = fetch_historical_candles(kite, token, from_date, to_date, "day")
        if not candles:
            print(f"  {symbol:<12} no candles returned")
            continue
        earliest = candles[0]["date"]
        latest = candles[-1]["date"]
        span_days = (latest - earliest).days if hasattr(latest, "days") else None
        span_days = (latest - earliest).days
        print(f"  {symbol:<12} earliest={earliest}  latest={latest}  span={span_days} days  candles={len(candles)}")


if __name__ == "__main__":
    main()
