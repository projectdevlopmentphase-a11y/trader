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
    exchange: str = "NSE",
) -> list[ScanCandidate]:
    """Scans the universe right now using Kite's quote API for today's open/
    volume-so-far/previous-close, and daily historical candles for the
    average-volume baseline. Meant to be called once shortly after market
    open (e.g. after the opening range completes) at the start of each
    trading day's run.
    """
    from datetime import timedelta

    instruments = [f"{exchange}:{symbol}" for symbol in universe]
    quotes = kite.quote(instruments)

    instrument_map = {i["tradingsymbol"]: i["instrument_token"] for i in kite.instruments(exchange)}

    to_date = datetime.now()
    from_date = to_date - timedelta(days=lookback_days * 2 + 5)

    candidates: list[ScanCandidate] = []
    for symbol in universe:
        quote = quotes.get(f"{exchange}:{symbol}")
        token = instrument_map.get(symbol)
        if quote is None or token is None:
            continue

        ohlc = quote.get("ohlc", {})
        prev_close = ohlc.get("close")
        today_open = ohlc.get("open")
        today_volume = quote.get("volume", 0)
        if not prev_close or not today_open:
            continue

        daily_candles = kite.historical_data(token, from_date, to_date, "day")
        volumes = [c["volume"] for c in daily_candles[-lookback_days:] if c.get("volume")]
        if len(volumes) < lookback_days:
            continue
        avg_daily_volume = sum(volumes) / len(volumes)

        candidate = build_candidate(symbol, prev_close, today_open, today_volume, avg_daily_volume)
        if candidate is not None:
            candidates.append(candidate)

    return candidates
