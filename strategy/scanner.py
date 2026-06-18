"""Daily watchlist scanner.

Instead of always trading the same fixed set of symbols regardless of
conditions, each trading day this picks the symbols from a broader universe
that are actually showing relative-volume and gap activity that morning.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ScanCandidate:
    symbol: str
    gap_pct: float
    relative_volume: float

    @property
    def score(self) -> float:
        return abs(self.gap_pct) * self.relative_volume


def build_candidate(
    symbol: str,
    prev_close: float | None,
    today_open: float,
    opening_volume: float,
    avg_opening_volume: float,
) -> ScanCandidate | None:
    if not prev_close or prev_close <= 0 or avg_opening_volume <= 0:
        return None
    gap_pct = (today_open - prev_close) / prev_close * 100
    relative_volume = opening_volume / avg_opening_volume
    return ScanCandidate(symbol, gap_pct, relative_volume)


def rank_candidates(
    candidates: list[ScanCandidate],
    top_n: int,
    min_relative_volume: float,
    min_gap_pct: float,
) -> list[str]:
    """Filters out candidates below the activity thresholds, then returns the
    top_n symbols ranked by relative-volume * gap-size (most active first)."""
    qualified = [
        c
        for c in candidates
        if c.relative_volume >= min_relative_volume and abs(c.gap_pct) >= min_gap_pct
    ]
    qualified.sort(key=lambda c: c.score, reverse=True)
    return [c.symbol for c in qualified[:top_n]]


def group_candles_by_day(candles: list[dict]) -> dict[str, list[dict]]:
    by_day: dict[str, list[dict]] = {}
    for candle in candles:
        d = candle["date"]
        day = d.date().isoformat() if isinstance(d, datetime) else str(d)[:10]
        by_day.setdefault(day, []).append(candle)
    return by_day


def compute_narrow_range_days(by_day: dict[str, list[dict]], nr7_lookback: int) -> dict[str, bool]:
    """For every day in a symbol's full candle history, determines whether
    that day qualifies as an NR7-style entry day: the prior trading day's
    range was the narrowest of the preceding nr7_lookback trading days.

    Computed from the symbol's complete historical series so the result
    doesn't depend on which days the symbol happened to be selected into a
    watchlist -- unlike ORBStrategy's own internal tracking, which only
    advances on candles it's actually fed.
    """
    if nr7_lookback <= 0:
        return {day: True for day in by_day}

    days = sorted(by_day.keys())
    ranges = [max(c["high"] for c in by_day[day]) - min(c["low"] for c in by_day[day]) for day in days]

    flags: dict[str, bool] = {}
    for i, day in enumerate(days):
        window = ranges[max(0, i - nr7_lookback) : i]
        flags[day] = len(window) == nr7_lookback and ranges[i - 1] == min(window)
    return flags


def scan_backtest_day(
    candles_by_symbol_day: dict[str, dict[str, list[dict]]],
    all_days: list[str],
    day: str,
    range_candles: int,
    lookback_days: int,
    last_close_per_symbol: dict[str, float],
) -> list[ScanCandidate]:
    """Builds today's scan candidates from already-fetched historical candles.

    For each symbol, the relative-volume baseline is the average opening-range
    volume over the `lookback_days` trading days preceding `day` (limited to
    days that symbol actually has data for).
    """
    prior_days = [d for d in all_days if d < day]
    candidates: list[ScanCandidate] = []

    for symbol, by_day in candles_by_symbol_day.items():
        day_candles = by_day.get(day)
        if not day_candles:
            continue

        lookback = [d for d in prior_days if d in by_day][-lookback_days:]
        if len(lookback) < lookback_days:
            continue

        opening_volumes = [sum(c["volume"] for c in by_day[d][:range_candles]) for d in lookback]
        avg_opening_volume = sum(opening_volumes) / len(opening_volumes)

        today_open = day_candles[0]["open"]
        today_opening_volume = sum(c["volume"] for c in day_candles[:range_candles])
        prev_close = last_close_per_symbol.get(symbol) or by_day[lookback[-1]][-1]["close"]

        candidate = build_candidate(symbol, prev_close, today_open, today_opening_volume, avg_opening_volume)
        if candidate is not None:
            candidates.append(candidate)

    return candidates


def scan_live_universe(
    kite,
    universe: list[str],
    lookback_days: int = 20,
    interval: str = "5minute",
    exchange: str = "NSE",
    now: datetime | None = None,
) -> list[ScanCandidate]:
    """Scans the universe right now using Kite's quote API for today's open/
    volume-so-far/previous-close. Meant to be called once shortly after
    market open (e.g. after the opening range completes) at the start of
    each trading day's run.

    Today's volume-so-far only covers however much of the session has
    elapsed, so the baseline must be the average volume traded in that SAME
    elapsed window (e.g. 9:15-9:30) over the lookback days -- comparing it
    against a full trading day's average would make relative volume read
    "low" for almost every symbol early in the session, regardless of
    actual activity.
    """
    from datetime import timedelta

    from data.historical import fetch_historical_candles

    now = now or datetime.now()
    cutoff_time = now.time()

    instruments = [f"{exchange}:{symbol}" for symbol in universe]
    quotes = kite.quote(instruments)

    instrument_map = {i["tradingsymbol"]: i["instrument_token"] for i in kite.instruments(exchange)}

    from_date = now - timedelta(days=lookback_days * 2 + 10)
    to_date = now - timedelta(days=1)  # prior days only, for the baseline

    candidates: list[ScanCandidate] = []
    for symbol in universe:
        quote = quotes.get(f"{exchange}:{symbol}")
        token = instrument_map.get(symbol)
        if quote is None or token is None:
            continue

        ohlc = quote.get("ohlc", {})
        prev_close = ohlc.get("close")
        today_open = ohlc.get("open")
        today_volume_so_far = quote.get("volume", 0)
        if not prev_close or not today_open:
            continue

        intraday_candles = fetch_historical_candles(kite, token, from_date, to_date, interval)
        by_day = group_candles_by_day(intraday_candles)

        elapsed_volumes = []
        for day_candles in by_day.values():
            elapsed = sum(
                c["volume"]
                for c in day_candles
                if not isinstance(c["date"], datetime) or c["date"].time() <= cutoff_time
            )
            elapsed_volumes.append(elapsed)
        elapsed_volumes = elapsed_volumes[-lookback_days:]
        if len(elapsed_volumes) < lookback_days:
            continue
        avg_elapsed_volume = sum(elapsed_volumes) / len(elapsed_volumes)

        candidate = build_candidate(symbol, prev_close, today_open, today_volume_so_far, avg_elapsed_volume)
        if candidate is not None:
            candidates.append(candidate)

    return candidates
