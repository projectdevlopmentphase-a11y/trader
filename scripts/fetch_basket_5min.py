"""Fetch 6 months of 5-minute candles for a fixed basket of symbols and dump
each to its own CSV under reports/, for quick offline inspection/backtesting.

Usage:
    python scripts/fetch_basket_5min.py
    python scripts/fetch_basket_5min.py --months 3 --symbols CIPLA MARUTI
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from auth.kite_auth import KiteAuth  # noqa: E402
from data.historical import fetch_historical_candles, fetch_instruments  # noqa: E402

_DEFAULT_SYMBOLS = [
    "CIPLA", "MARUTI", "SUNPHARMA", "DRREDDY", "ASIANPAINT", "POWERGRID", "SHRIRAMFIN",
]
_OUTPUT_DIR = REPO_ROOT / "reports" / "basket_5min"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=_DEFAULT_SYMBOLS)
    parser.add_argument("--months", type=int, default=6)
    args = parser.parse_args()

    auth = KiteAuth()
    kite = auth.authenticated_client()

    instruments = fetch_instruments(kite, "NSE")
    symbol_to_token = {i["tradingsymbol"]: i["instrument_token"] for i in instruments}

    to_date = datetime.now()
    from_date = to_date - timedelta(days=args.months * 30)

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Fetching {args.months} months of 5minute candles ({from_date.date()} -> {to_date.date()})\n")

    for symbol in args.symbols:
        token = symbol_to_token.get(symbol)
        if token is None:
            print(f"  {symbol:<12} unknown symbol")
            continue
        candles = fetch_historical_candles(kite, token, from_date, to_date, "5minute")
        if not candles:
            print(f"  {symbol:<12} no candles returned")
            continue

        out_path = _OUTPUT_DIR / f"{symbol}.csv"
        with open(out_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
            writer.writeheader()
            writer.writerows(candles)

        print(f"  {symbol:<12} candles={len(candles):<6} -> {out_path}")


if __name__ == "__main__":
    main()
