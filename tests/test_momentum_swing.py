from datetime import date, datetime, timedelta

from strategy.base import Action
from strategy.momentum_swing import MomentumSwingStrategy


def make_candle(day_offset, close, base=None):
    base = base or datetime(2024, 1, 1)
    d = base + timedelta(days=day_offset)
    return {"date": d, "open": close, "high": close, "low": close, "close": close, "volume": 1000}


def make_strategy(**overrides):
    params = dict(lookback_months=2, skip_months=0, top_n=2, rebalance_day_of_month=1, stop_pct=10.0)
    params.update(overrides)
    return MomentumSwingStrategy(**params)


def _feed_history(strategy, symbol, closes, base):
    for i, close in enumerate(closes):
        strategy.on_candle(symbol, make_candle(i, close, base=base))


def test_no_rebalance_without_enough_history():
    strategy = make_strategy()
    base = datetime(2024, 1, 1)
    _feed_history(strategy, "A", [100] * 10, base)
    signals = strategy.compute_rebalance(date(2024, 1, 1))
    assert signals == []


def test_ranks_top_n_and_emits_buy_signals():
    strategy = make_strategy()
    base = datetime(2024, 1, 1)
    lookback_days = 2 * 22  # lookback_months * 22

    # A: strong uptrend, B: strong uptrend, C: flat, D: downtrend.
    _feed_history(strategy, "A", [100 + i for i in range(lookback_days)], base)
    _feed_history(strategy, "B", [100 + 2 * i for i in range(lookback_days)], base)
    _feed_history(strategy, "C", [100 for _ in range(lookback_days)], base)
    _feed_history(strategy, "D", [100 - i for i in range(lookback_days)], base)

    signals = strategy.compute_rebalance(date(2024, 6, 1))
    buys = {s.symbol for s in signals if s.action == Action.BUY}
    assert buys == {"A", "B"}
    assert all(s.reason for s in signals)


def test_does_not_double_fire_within_same_month():
    strategy = make_strategy()
    base = datetime(2024, 1, 1)
    lookback_days = 2 * 22
    _feed_history(strategy, "A", [100 + i for i in range(lookback_days)], base)
    _feed_history(strategy, "B", [100 + 2 * i for i in range(lookback_days)], base)

    first = strategy.compute_rebalance(date(2024, 6, 1))
    assert first != []

    second = strategy.compute_rebalance(date(2024, 6, 15))
    assert second == []


def test_membership_change_emits_buy_and_exit():
    strategy = make_strategy()
    base = datetime(2024, 1, 1)
    lookback_days = 2 * 22

    _feed_history(strategy, "A", [100 + i for i in range(lookback_days)], base)
    _feed_history(strategy, "B", [100 + 2 * i for i in range(lookback_days)], base)
    _feed_history(strategy, "C", [100 - i for i in range(lookback_days)], base)

    first = strategy.compute_rebalance(date(2024, 6, 1))
    first_buys = {s.symbol for s in first if s.action == Action.BUY}
    assert first_buys == {"A", "B"}

    # Next month: C surges while A goes flat and B keeps rising modestly,
    # so A (the worst performer of the three) drops out of the top-2.
    next_base = base + timedelta(days=lookback_days)
    _feed_history(strategy, "C", [200 + 5 * i for i in range(lookback_days)], next_base)
    _feed_history(strategy, "B", [100 + i for i in range(lookback_days)], next_base)
    _feed_history(strategy, "A", [100] * lookback_days, next_base)

    second = strategy.compute_rebalance(date(2024, 7, 1))
    second_buys = {s.symbol for s in second if s.action == Action.BUY}
    second_exits = {s.symbol for s in second if s.action == Action.EXIT}
    assert "C" in second_buys
    assert "A" in second_exits
