"""One-off diagnostic: runs the actual ORB strategy (not just the scanner)
over the backtest window and tallies, per month, how many days had the
narrow-range (NR7) filter open vs closed, and how many actual entry
signals fired. Use this to find out *why* trades cluster in one month --
the previous diagnostic (diagnose_backtest.py) proved the scanner qualifies
candidates evenly across the whole window, so if entries still cluster,
the cause must be inside ORBStrategy itself (most likely the NR7 lookback
warm-up, which only advances a symbol's "day" counter on days that symbol
is actually selected into the watchlist).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, time as dt_time, timedelta

from auth.kite_auth import KiteAuth
from config.settings import settings
from data.historical import fetch_historical_candles
from strategy.base import Action
from strategy.orb import ORBStrategy
from strategy.scanner import compute_narrow_range_days, group_candles_by_day, rank_candidates, scan_backtest_day

auth = KiteAuth()
kite = auth.authenticated_client()

instruments = kite.instruments("NSE")
symbol_to_token = {i["tradingsymbol"]: i["instrument_token"] for i in instruments}

universe = settings.scan_universe or settings.watchlist
range_candles = max(1, settings.orb_range_minutes // int(settings.orb_candle_interval.replace("minute", "") or 1))
square_off_hour, square_off_minute = (int(part) for part in settings.square_off_time.split(":"))
square_off_cutoff = dt_time(square_off_hour, square_off_minute)

backtest_start = datetime.now() - timedelta(days=settings.backtest_days)
from_date = backtest_start - timedelta(days=settings.scan_lookback_days * 2 + 5)
to_date = datetime.now()

candles_by_symbol_day = {}
for symbol in universe:
    token = symbol_to_token.get(symbol)
    if token is None:
        continue
    candles = fetch_historical_candles(kite, token, from_date, to_date, settings.orb_candle_interval)
    candles_by_symbol_day[symbol] = group_candles_by_day(candles)

all_days = sorted({day for by_day in candles_by_symbol_day.values() for day in by_day})
backtest_start_day = backtest_start.date().isoformat()
trading_days = [day for day in all_days if day >= backtest_start_day]

narrow_range_days = {
    symbol: compute_narrow_range_days(by_day, settings.orb_nr7_lookback)
    for symbol, by_day in candles_by_symbol_day.items()
}

strategy = ORBStrategy(
    settings.orb_range_minutes,
    settings.orb_candle_interval,
    settings.orb_volume_multiplier,
    settings.orb_volume_lookback,
    settings.orb_nr7_lookback,
    settings.orb_entry_cutoff_time,
    settings.orb_target_r,
)

watchlist_days_by_month = Counter()
narrow_open_by_month = Counter()
entries_by_month = Counter()
watchlist_by_symbol_month = Counter()
narrow_open_by_symbol_month = Counter()
entries_by_symbol_month = Counter()
last_close_per_symbol = {}

for day in trading_days:
    month = day[:7]
    candidates = scan_backtest_day(
        candles_by_symbol_day, all_days, day, range_candles, settings.scan_lookback_days, last_close_per_symbol
    )
    todays_watchlist = rank_candidates(
        candidates, settings.scan_top_n, settings.scan_min_relative_volume, settings.scan_min_gap_pct
    )

    for symbol in todays_watchlist:
        watchlist_days_by_month[month] += 1
        watchlist_by_symbol_month[(symbol, month)] += 1
        day_candles = candles_by_symbol_day[symbol][day]
        tradable_candles = [
            c for c in day_candles if not isinstance(c["date"], datetime) or c["date"].time() <= square_off_cutoff
        ]
        if not tradable_candles:
            continue

        is_narrow_range_day = narrow_range_days[symbol].get(day, False)
        for candle in tradable_candles:
            signal = strategy.on_candle(symbol, {**candle, "_narrow_range_day": is_narrow_range_day})
            if signal is not None and signal.action in (Action.BUY, Action.SELL):
                entries_by_month[month] += 1
                entries_by_symbol_month[(symbol, month)] += 1

        if is_narrow_range_day:
            narrow_open_by_month[month] += 1
            narrow_open_by_symbol_month[(symbol, month)] += 1

    for symbol, by_day in candles_by_symbol_day.items():
        day_candles = by_day.get(day)
        if day_candles:
            last_close_per_symbol[symbol] = day_candles[-1]["close"]

months = sorted(set(watchlist_days_by_month) | set(narrow_open_by_month) | set(entries_by_month))
print(f"{'month':<10}{'watchlist_slots':>16}{'narrow_open':>14}{'entries':>10}")
for month in months:
    print(f"{month:<10}{watchlist_days_by_month[month]:>16}{narrow_open_by_month[month]:>14}{entries_by_month[month]:>10}")

print("\nPer-symbol per-month breakdown:")
print(f"{'symbol':<14}{'month':<10}{'watchlist_slots':>16}{'narrow_open':>14}{'entries':>10}")
for symbol in universe:
    for month in months:
        slots = watchlist_by_symbol_month.get((symbol, month), 0)
        if slots == 0:
            continue
        narrow = narrow_open_by_symbol_month.get((symbol, month), 0)
        entries = entries_by_symbol_month.get((symbol, month), 0)
        print(f"{symbol:<14}{month:<10}{slots:>16}{narrow:>14}{entries:>10}")
