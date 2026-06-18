from datetime import datetime, timedelta

from strategy.scanner import (
    ScanCandidate,
    build_candidate,
    group_candles_by_day,
    rank_candidates,
    scan_backtest_day,
)


def test_build_candidate_computes_gap_and_relative_volume():
    candidate = build_candidate("TEST", prev_close=100, today_open=105, opening_volume=3000, avg_opening_volume=1000)
    assert candidate.symbol == "TEST"
    assert candidate.gap_pct == 5.0
    assert candidate.relative_volume == 3.0


def test_build_candidate_returns_none_without_baseline():
    assert build_candidate("TEST", prev_close=None, today_open=105, opening_volume=3000, avg_opening_volume=1000) is None
    assert build_candidate("TEST", prev_close=100, today_open=105, opening_volume=3000, avg_opening_volume=0) is None


def test_rank_candidates_filters_and_orders_by_score():
    candidates = [
        ScanCandidate("LOW_ACTIVITY", gap_pct=0.2, relative_volume=1.1),
        ScanCandidate("BEST", gap_pct=3.0, relative_volume=4.0),
        ScanCandidate("OK", gap_pct=1.0, relative_volume=2.0),
    ]
    result = rank_candidates(candidates, top_n=2, min_relative_volume=1.5, min_gap_pct=0.5)
    assert result == ["BEST", "OK"]


def make_candle(minutes_from_open, o, h, l, c, day, volume=1000):
    base = datetime.fromisoformat(day) + timedelta(hours=9, minutes=15)
    return {
        "date": base + timedelta(minutes=minutes_from_open),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": volume,
    }


def test_group_candles_by_day():
    candles = [
        make_candle(0, 100, 101, 99, 100, "2026-01-01"),
        make_candle(5, 100, 101, 99, 100, "2026-01-01"),
        make_candle(0, 100, 101, 99, 100, "2026-01-02"),
    ]
    by_day = group_candles_by_day(candles)
    assert set(by_day.keys()) == {"2026-01-01", "2026-01-02"}
    assert len(by_day["2026-01-01"]) == 2


def test_scan_backtest_day_skips_symbols_without_enough_lookback():
    candles_by_symbol_day = {
        "ONLY_ONE_DAY": {"2026-01-10": [make_candle(0, 100, 101, 99, 100, "2026-01-10", volume=5000)]},
    }
    candidates = scan_backtest_day(
        candles_by_symbol_day,
        all_days=["2026-01-10"],
        day="2026-01-10",
        range_candles=1,
        lookback_days=3,
        last_close_per_symbol={},
    )
    assert candidates == []


def test_scan_backtest_day_finds_active_symbol():
    days = ["2026-01-0%d" % d for d in range(5, 9)]  # 4 prior days
    today = "2026-01-09"
    by_day = {}
    for day in days:
        by_day[day] = [make_candle(0, 100, 101, 99, 100, day, volume=1000)]
    by_day[today] = [make_candle(0, 110, 112, 109, 111, today, volume=5000)]

    candidates = scan_backtest_day(
        {"ACTIVE": by_day},
        all_days=days + [today],
        day=today,
        range_candles=1,
        lookback_days=4,
        last_close_per_symbol={"ACTIVE": 100},
    )
    assert len(candidates) == 1
    assert candidates[0].symbol == "ACTIVE"
    assert candidates[0].relative_volume == 5.0
    assert candidates[0].gap_pct == 10.0
