from datetime import datetime, timedelta

from strategy.base import Action
from strategy.orb import ORBStrategy


def make_candle(day_offset_minutes, o, h, l, c, base=None, volume=1000):
    base = base or datetime(2024, 1, 1, 9, 15)
    return {
        "date": base + timedelta(minutes=day_offset_minutes),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": volume,
    }


def make_strategy(**overrides):
    params = dict(
        range_minutes=15,
        candle_interval="5minute",
        volume_multiplier=0,
        nr7_lookback=0,
        entry_cutoff_time=None,
    )
    params.update(overrides)
    return ORBStrategy(**params)


def test_no_signal_during_range_formation():
    strategy = make_strategy()
    candle = make_candle(0, 100, 101, 99, 100)
    assert strategy.on_candle("TEST", candle) is None


def test_breakout_above_range_triggers_buy():
    strategy = make_strategy()
    strategy.on_candle("TEST", make_candle(0, 100, 102, 99, 101))
    strategy.on_candle("TEST", make_candle(5, 101, 102, 100, 101))
    strategy.on_candle("TEST", make_candle(10, 101, 103, 100, 102))

    signal = strategy.on_candle("TEST", make_candle(15, 102, 110, 102, 108))
    assert signal is not None
    assert signal.action == Action.BUY


def test_breakdown_below_range_triggers_sell():
    strategy = make_strategy()
    strategy.on_candle("TEST", make_candle(0, 100, 102, 99, 101))
    strategy.on_candle("TEST", make_candle(5, 101, 102, 100, 101))
    strategy.on_candle("TEST", make_candle(10, 101, 103, 99, 100))

    signal = strategy.on_candle("TEST", make_candle(15, 99, 99, 90, 92))
    assert signal is not None
    assert signal.action == Action.SELL


def test_new_day_resets_range():
    strategy = make_strategy()
    day1 = datetime(2024, 1, 1, 9, 15)
    day2 = datetime(2024, 1, 2, 9, 15)

    strategy.on_candle("TEST", make_candle(0, 100, 102, 99, 101, base=day1))
    strategy.on_candle("TEST", make_candle(5, 101, 102, 100, 101, base=day1))
    strategy.on_candle("TEST", make_candle(10, 101, 103, 99, 100, base=day1))
    strategy.on_candle("TEST", make_candle(15, 102, 110, 102, 108, base=day1))

    signal = strategy.on_candle("TEST", make_candle(0, 200, 201, 199, 200, base=day2))
    assert signal is None


def test_volume_filter_blocks_low_volume_breakout():
    strategy = make_strategy(volume_multiplier=1.5)
    strategy.on_candle("TEST", make_candle(0, 100, 102, 99, 101, volume=1000))
    strategy.on_candle("TEST", make_candle(5, 101, 102, 100, 101, volume=1000))
    strategy.on_candle("TEST", make_candle(10, 101, 103, 100, 102, volume=1000))

    # Breakout candle's volume is below 1.5x the recent average -> blocked.
    signal = strategy.on_candle("TEST", make_candle(15, 102, 110, 102, 108, volume=1000))
    assert signal is None


def test_volume_filter_allows_high_volume_breakout():
    strategy = make_strategy(volume_multiplier=1.5)
    strategy.on_candle("TEST", make_candle(0, 100, 102, 99, 101, volume=1000))
    strategy.on_candle("TEST", make_candle(5, 101, 102, 100, 101, volume=1000))
    strategy.on_candle("TEST", make_candle(10, 101, 103, 100, 102, volume=1000))

    signal = strategy.on_candle("TEST", make_candle(15, 102, 110, 102, 108, volume=5000))
    assert signal is not None
    assert signal.action == Action.BUY


def test_nr7_filter_blocks_entry_without_narrow_range_history():
    # Default nr7_lookback=7 with no prior-day history at all -> can't confirm
    # a narrow-range day, so entries are blocked.
    strategy = make_strategy(nr7_lookback=7)
    strategy.on_candle("TEST", make_candle(0, 100, 102, 99, 101))
    strategy.on_candle("TEST", make_candle(5, 101, 102, 100, 101))
    strategy.on_candle("TEST", make_candle(10, 101, 103, 100, 102))

    signal = strategy.on_candle("TEST", make_candle(15, 102, 110, 102, 108))
    assert signal is None


def test_time_cutoff_blocks_late_entry():
    strategy = make_strategy(entry_cutoff_time="13:00")
    base = datetime(2024, 1, 1, 13, 30)
    strategy.on_candle("TEST", make_candle(0, 100, 102, 99, 101, base=base))
    strategy.on_candle("TEST", make_candle(5, 101, 102, 100, 101, base=base))
    strategy.on_candle("TEST", make_candle(10, 101, 103, 100, 102, base=base))

    # Range forms at 13:40-ish, well past the 13:00 cutoff -> no new entry.
    signal = strategy.on_candle("TEST", make_candle(15, 102, 110, 102, 108, base=base))
    assert signal is None
