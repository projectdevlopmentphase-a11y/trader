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


def test_on_candle_exits_when_stop_pct_breached_mid_month():
    strategy = make_strategy(stop_pct=10.0)
    base = datetime(2024, 1, 1)
    lookback_days = 2 * 22

    _feed_history(strategy, "A", [100 + i for i in range(lookback_days)], base)
    _feed_history(strategy, "B", [100 + 2 * i for i in range(lookback_days)], base)

    signals = strategy.compute_rebalance(date(2024, 6, 1))
    entry_price = next(s.price for s in signals if s.symbol == "A")

    # A close more than 10% below the entry price should trigger an
    # immediate stop-loss exit rather than waiting for next month's
    # rebalance to notice A fell out of the top-N.
    stop_candle = make_candle(0, entry_price * 0.85, base=base + timedelta(days=lookback_days))
    signal = strategy.on_candle("A", stop_candle)

    assert signal is not None
    assert signal.action == Action.EXIT
    assert signal.symbol == "A"


def test_on_candle_does_not_exit_when_above_stop():
    strategy = make_strategy(stop_pct=10.0)
    base = datetime(2024, 1, 1)
    lookback_days = 2 * 22

    _feed_history(strategy, "A", [100 + i for i in range(lookback_days)], base)
    _feed_history(strategy, "B", [100 + 2 * i for i in range(lookback_days)], base)

    signals = strategy.compute_rebalance(date(2024, 6, 1))
    entry_price = next(s.price for s in signals if s.symbol == "A")

    near_stop_candle = make_candle(0, entry_price * 0.95, base=base + timedelta(days=lookback_days))
    signal = strategy.on_candle("A", near_stop_candle)
    assert signal is None


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

    # Next month: C surges while A goes flat (just under its 143 entry
    # price, but not far enough to breach the 10% stop) and B keeps rising
    # modestly, so A (the worst performer of the three) drops out of the
    # top-2 via the rebalance rather than an on_candle stop-loss exit.
    next_base = base + timedelta(days=lookback_days)
    _feed_history(strategy, "C", [200 + 5 * i for i in range(lookback_days)], next_base)
    _feed_history(strategy, "B", [100 + i for i in range(lookback_days)], next_base)
    _feed_history(strategy, "A", [140] * lookback_days, next_base)

    second = strategy.compute_rebalance(date(2024, 7, 1))
    second_buys = {s.symbol for s in second if s.action == Action.BUY}
    second_exits = {s.symbol for s in second if s.action == Action.EXIT}
    assert "C" in second_buys
    assert "A" in second_exits


def test_stopped_out_symbol_excluded_from_reentry_during_cooldown():
    strategy = make_strategy(top_n=1, stop_cooldown_months=1)
    base = datetime(2024, 1, 1)
    lookback_days = 2 * 22

    # A is the clear momentum leader; B is far behind.
    _feed_history(strategy, "A", [100 + i for i in range(lookback_days)], base)
    _feed_history(strategy, "B", [100 + 0.1 * i for i in range(lookback_days)], base)

    signals = strategy.compute_rebalance(date(2024, 6, 1))
    entry_price = next(s.price for s in signals if s.symbol == "A")
    assert {s.symbol for s in signals if s.action == Action.BUY} == {"A"}

    # A gets stopped out mid-June.
    stop_candle = make_candle(0, entry_price * 0.85, base=datetime(2024, 6, 15))
    stop_signal = strategy.on_candle("A", stop_candle)
    assert stop_signal is not None and stop_signal.action == Action.EXIT

    # A is still fed strong continued uptrend data, so it would still rank
    # #1 by momentum next month -- but it should be skipped during cooldown,
    # falling back to B instead of whipsawing straight back into A.
    next_base = base + timedelta(days=lookback_days)
    _feed_history(strategy, "A", [100 + i for i in range(lookback_days)], next_base)
    _feed_history(strategy, "B", [100 + 0.1 * i for i in range(lookback_days)], next_base)

    second = strategy.compute_rebalance(date(2024, 7, 1))
    second_buys = {s.symbol for s in second if s.action == Action.BUY}
    assert "A" not in second_buys
    assert second_buys == {"B"}

    # After the cooldown period elapses, A becomes eligible again.
    third_base = next_base + timedelta(days=lookback_days)
    _feed_history(strategy, "A", [100 + i for i in range(lookback_days)], third_base)
    _feed_history(strategy, "B", [100 + 0.1 * i for i in range(lookback_days)], third_base)

    third = strategy.compute_rebalance(date(2024, 8, 1))
    third_buys = {s.symbol for s in third if s.action == Action.BUY}
    assert "A" in third_buys
