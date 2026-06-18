"""One-off diagnostic: shows, per trading day, whether the scanner found any
qualifying candidates and how many. Run with the same env as main.py to see
exactly where the backtest's tradable-day count drops off, instead of
guessing from the final trade log.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from auth.kite_auth import KiteAuth
from config.settings import settings
from data.historical import fetch_historical_candles
from strategy.scanner import group_candles_by_day, rank_candidates, scan_backtest_day

auth = KiteAuth()
kite = auth.authenticated_client()

instruments = kite.instruments("NSE")
symbol_to_token = {i["tradingsymbol"]: i["instrument_token"] for i in instruments}

universe = settings.scan_universe or settings.watchlist
range_candles = max(1, settings.orb_range_minutes // int(settings.orb_candle_interval.replace("minute", "") or 1))

backtest_start = datetime.now() - timedelta(days=settings.backtest_days)
from_date = backtest_start - timedelta(days=settings.scan_lookback_days * 2 + 5)
to_date = datetime.now()

candles_by_symbol_day = {}
for symbol in universe:
    token = symbol_to_token.get(symbol)
    if token is None:
        print(f"SKIP unknown symbol: {symbol}")
        continue
    candles = fetch_historical_candles(kite, token, from_date, to_date, settings.orb_candle_interval)
    by_day = group_candles_by_day(candles)
    candles_by_symbol_day[symbol] = by_day
    days_for_symbol = sorted(by_day.keys())
    print(f"{symbol}: {len(days_for_symbol)} days fetched, range {days_for_symbol[0]} .. {days_for_symbol[-1]}")

all_days = sorted({day for by_day in candles_by_symbol_day.values() for day in by_day})
backtest_start_day = backtest_start.date().isoformat()
trading_days = [day for day in all_days if day >= backtest_start_day]
print(f"\nTotal trading_days in backtest window: {len(trading_days)} ({trading_days[0]} .. {trading_days[-1]})")

last_close_per_symbol = {}
qualifying_days = []
candidate_days = []
for day in trading_days:
    candidates = scan_backtest_day(
        candles_by_symbol_day, all_days, day, range_candles, settings.scan_lookback_days, last_close_per_symbol
    )
    if candidates:
        candidate_days.append((day, len(candidates)))
    todays_watchlist = rank_candidates(
        candidates, settings.scan_top_n, settings.scan_min_relative_volume, settings.scan_min_gap_pct
    )
    if todays_watchlist:
        qualifying_days.append((day, todays_watchlist))

    for symbol, by_day in candles_by_symbol_day.items():
        day_candles = by_day.get(day)
        if day_candles:
            last_close_per_symbol[symbol] = day_candles[-1]["close"]

print(f"\nDays where ANY candidate passed build_candidate() (has baseline data): {len(candidate_days)}")
print(f"Days where rank_candidates() qualified at least one symbol (gap/rel-vol thresholds met): {len(qualifying_days)}")
print("\nQualifying days and symbols:")
for day, symbols in qualifying_days:
    print(f"  {day}: {symbols}")
