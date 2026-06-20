from datetime import datetime, timedelta

from strategy.base import Action
from strategy.breakout_swing import BreakoutSwingStrategy


def make_candle(day_offset, o, h, l, c, base=None):
    base = base or datetime(2024, 1, 1)
    return {
        "date": base + timedelta(days=day_offset),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1000,
    }


def make_strategy(**overrides):
    params = dict(lookback_days=5, trailing_stop_days=3)
    params.update(overrides)
    return BreakoutSwingStrategy(**params)


def test_no_signal_before_lookback_window_full():
    strategy = make_strategy()
    for i in range(4):
        signal = strategy.on_candle("TEST", make_candle(i, 100, 101, 99, 100))
        assert signal is None


def test_breakout_above_n_day_high_triggers_buy():
    strategy = make_strategy()
    for i in range(5):
        strategy.on_candle("TEST", make_candle(i, 100, 101, 99, 100))

    signal = strategy.on_candle("TEST", make_candle(5, 101, 110, 101, 109))
    assert signal is not None
    assert signal.action == Action.BUY


def test_no_short_signal_ever():
    strategy = make_strategy()
    candles = [make_candle(i, 100 - i, 101 - i, 95 - i, 96 - i) for i in range(20)]
    for candle in candles:
        signal = strategy.on_candle("TEST", candle)
        assert signal is None or signal.action in (Action.BUY, Action.EXIT)


def test_exit_on_trailing_stop_break():
    strategy = make_strategy()
    for i in range(5):
        strategy.on_candle("TEST", make_candle(i, 100, 101, 99, 100))
    signal = strategy.on_candle("TEST", make_candle(5, 101, 110, 101, 109))
    assert signal.action == Action.BUY

    strategy.on_candle("TEST", make_candle(6, 109, 110, 105, 107))
    strategy.on_candle("TEST", make_candle(7, 107, 108, 104, 106))

    signal = strategy.on_candle("TEST", make_candle(8, 106, 106, 100, 95))
    assert signal is not None
    assert signal.action == Action.EXIT


def test_no_duplicate_entry_while_in_position():
    strategy = make_strategy()
    for i in range(5):
        strategy.on_candle("TEST", make_candle(i, 100, 101, 99, 100))
    signal = strategy.on_candle("TEST", make_candle(5, 101, 110, 101, 109))
    assert signal.action == Action.BUY

    signal = strategy.on_candle("TEST", make_candle(6, 109, 120, 109, 119))
    assert signal is None
