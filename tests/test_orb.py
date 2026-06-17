from datetime import datetime, timedelta

from strategy.base import Action
from strategy.orb import ORBStrategy


def make_candle(day_offset_minutes, o, h, l, c, base=None):
    base = base or datetime(2024, 1, 1, 9, 15)
    return {
        "date": base + timedelta(minutes=day_offset_minutes),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1000,
    }


def test_no_signal_during_range_formation():
    strategy = ORBStrategy(range_minutes=15, candle_interval="5minute")
    candle = make_candle(0, 100, 101, 99, 100)
    assert strategy.on_candle("TEST", candle) is None


def test_breakout_above_range_triggers_buy():
    strategy = ORBStrategy(range_minutes=15, candle_interval="5minute")
    strategy.on_candle("TEST", make_candle(0, 100, 102, 99, 101))
    strategy.on_candle("TEST", make_candle(5, 101, 102, 100, 101))
    strategy.on_candle("TEST", make_candle(10, 101, 103, 100, 102))

    signal = strategy.on_candle("TEST", make_candle(15, 102, 110, 102, 108))
    assert signal is not None
    assert signal.action == Action.BUY


def test_breakdown_below_range_triggers_sell():
    strategy = ORBStrategy(range_minutes=15, candle_interval="5minute")
    strategy.on_candle("TEST", make_candle(0, 100, 102, 99, 101))
    strategy.on_candle("TEST", make_candle(5, 101, 102, 100, 101))
    strategy.on_candle("TEST", make_candle(10, 101, 103, 99, 100))

    signal = strategy.on_candle("TEST", make_candle(15, 99, 99, 90, 92))
    assert signal is not None
    assert signal.action == Action.SELL


def test_new_day_resets_range():
    strategy = ORBStrategy(range_minutes=15, candle_interval="5minute")
    day1 = datetime(2024, 1, 1, 9, 15)
    day2 = datetime(2024, 1, 2, 9, 15)

    strategy.on_candle("TEST", make_candle(0, 100, 102, 99, 101, base=day1))
    strategy.on_candle("TEST", make_candle(5, 101, 102, 100, 101, base=day1))
    strategy.on_candle("TEST", make_candle(10, 101, 103, 99, 100, base=day1))
    strategy.on_candle("TEST", make_candle(15, 102, 110, 102, 108, base=day1))

    signal = strategy.on_candle("TEST", make_candle(0, 200, 201, 199, 200, base=day2))
    assert signal is None
