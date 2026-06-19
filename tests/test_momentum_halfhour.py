from datetime import datetime, timedelta

from strategy.base import Action
from strategy.momentum_halfhour import MomentumHalfHourStrategy


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


def run_day(strategy, symbol, base, closes, signal_window_minutes=30, interval=5):
    """Feeds one candle every `interval` minutes starting at `base`, returning
    the signal from the candle that closes the signal window (the only one
    that can ever emit a signal for this strategy)."""
    last_signal = None
    for i, close in enumerate(closes):
        offset = i * interval
        candle = make_candle(offset, close, close, close, close, base=base)
        signal = strategy.on_candle(symbol, candle)
        if offset >= signal_window_minutes:
            last_signal = last_signal or signal
    return last_signal


def test_no_signal_on_first_day_without_prior_close():
    strategy = MomentumHalfHourStrategy(signal_window_minutes=30)
    base = datetime(2024, 1, 1, 9, 15)
    # 7 candles of 5 minutes spans the 30-minute window and one candle past it.
    signal = run_day(strategy, "TEST", base, [100, 101, 102, 103, 104, 105, 106])
    assert signal is None


def test_long_signal_when_first_window_is_up():
    strategy = MomentumHalfHourStrategy(signal_window_minutes=30)
    day1 = datetime(2024, 1, 1, 9, 15)
    day2 = datetime(2024, 1, 2, 9, 15)

    run_day(strategy, "TEST", day1, [100, 100, 100, 100, 100, 100, 100])
    signal = run_day(strategy, "TEST", day2, [101, 102, 103, 104, 105, 106, 110])

    assert signal is not None
    assert signal.action == Action.BUY


def test_short_signal_when_first_window_is_down():
    strategy = MomentumHalfHourStrategy(signal_window_minutes=30)
    day1 = datetime(2024, 1, 1, 9, 15)
    day2 = datetime(2024, 1, 2, 9, 15)

    run_day(strategy, "TEST", day1, [100, 100, 100, 100, 100, 100, 100])
    signal = run_day(strategy, "TEST", day2, [99, 98, 97, 96, 95, 94, 90])

    assert signal is not None
    assert signal.action == Action.SELL


def test_only_one_signal_per_day():
    strategy = MomentumHalfHourStrategy(signal_window_minutes=30)
    day1 = datetime(2024, 1, 1, 9, 15)
    day2 = datetime(2024, 1, 2, 9, 15)

    run_day(strategy, "TEST", day1, [100, 100, 100, 100, 100, 100, 100])
    base = day2
    signals = []
    for i, close in enumerate([101, 102, 103, 104, 105, 106, 110, 111]):
        candle = make_candle(i * 5, close, close, close, close, base=base)
        signal = strategy.on_candle("TEST", candle)
        if signal is not None:
            signals.append(signal)
    assert len(signals) == 1


def test_volume_filter_blocks_low_volume_window():
    strategy = MomentumHalfHourStrategy(signal_window_minutes=30, volume_multiplier=1.5, volume_lookback=1)
    day1 = datetime(2024, 1, 1, 9, 15)
    day2 = datetime(2024, 1, 2, 9, 15)
    day3 = datetime(2024, 1, 3, 9, 15)

    # Day 1: establish a baseline window volume.
    for i, close in enumerate([100, 100, 100, 100, 100, 100, 100]):
        candle = make_candle(i * 5, close, close, close, close, base=day1, volume=1000)
        strategy.on_candle("TEST", candle)

    # Day 2: same low volume as the baseline -> blocked despite an up move.
    signal = None
    for i, close in enumerate([101, 102, 103, 104, 105, 106, 110]):
        candle = make_candle(i * 5, close, close, close, close, base=day2, volume=1000)
        signal = strategy.on_candle("TEST", candle) or signal
    assert signal is None

    # Day 3: much higher volume -> allowed.
    signal = None
    for i, close in enumerate([111, 112, 113, 114, 115, 116, 120]):
        candle = make_candle(i * 5, close, close, close, close, base=day3, volume=5000)
        signal = strategy.on_candle("TEST", candle) or signal
    assert signal is not None
    assert signal.action == Action.BUY


def test_external_volume_ok_override():
    strategy = MomentumHalfHourStrategy(signal_window_minutes=30, volume_multiplier=1.5)
    day1 = datetime(2024, 1, 1, 9, 15)
    day2 = datetime(2024, 1, 2, 9, 15)

    for i, close in enumerate([100, 100, 100, 100, 100, 100, 100]):
        strategy.on_candle("TEST", make_candle(i * 5, close, close, close, close, base=day1))

    signal = None
    for i, close in enumerate([101, 102, 103, 104, 105, 106, 110]):
        candle = make_candle(i * 5, close, close, close, close, base=day2, volume=1)
        candle["_volume_ok"] = True
        signal = strategy.on_candle("TEST", candle) or signal
    assert signal is not None
    assert signal.action == Action.BUY
