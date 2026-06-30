from datetime import datetime, timedelta

from strategy.base import Action
from strategy.vwap_breakout import VWAPBreakoutStrategy


def make_candle(dt: datetime, open_: float, high: float, low: float, close: float, volume: float) -> dict:
    return {"date": dt, "open": open_, "high": high, "low": low, "close": close, "volume": volume}


def flat(dt: datetime, price: float, volume: float = 1000) -> dict:
    return make_candle(dt, price, price, price, price, volume)


def make_strategy(**kwargs):
    defaults = dict(stop_pct=1.5, min_candles_after_open=2, volume_multiplier=1.5, volume_lookback=3)
    defaults.update(kwargs)
    return VWAPBreakoutStrategy(**defaults)


# Base datetime for tests: 2024-01-02 09:15 (first candle of day)
_BASE = datetime(2024, 1, 2, 9, 15)


def _dt(minutes_from_open: int) -> datetime:
    return _BASE + timedelta(minutes=minutes_from_open)


def test_no_signal_during_open_filter():
    """No BUY should fire within the first min_candles_after_open candles
    even if the cross occurs immediately."""
    strategy = make_strategy(min_candles_after_open=3, volume_multiplier=1.0)
    signals = []
    # Feed candles below then above VWAP within the blocked window.
    signals.append(strategy.on_candle("CIPLA", flat(_dt(0), 100, volume=1000)))   # candle 1
    signals.append(strategy.on_candle("CIPLA", flat(_dt(5), 98, volume=1000)))    # candle 2, below avg
    signals.append(strategy.on_candle("CIPLA", flat(_dt(10), 110, volume=3000)))  # candle 3, spike above
    assert all(s is None or s.action != Action.BUY for s in signals)


def test_buy_on_vwap_cross_with_volume():
    """BUY fires when price crosses above VWAP with sufficient volume."""
    strategy = make_strategy(volume_multiplier=1.5)

    # Warm up enough candles to be past the open filter (min_candles_after_open=2).
    # Use low volume so the first candles set a baseline and then spike.
    candles = [
        flat(_dt(0 + i * 5), 100, volume=1000) for i in range(3)
    ]
    for c in candles:
        sig = strategy.on_candle("CIPLA", c)
        assert sig is None

    # Previous candle: close below the running VWAP (push down).
    sig = strategy.on_candle("CIPLA", flat(_dt(15), 97, volume=1000))
    assert sig is None

    # Cross candle: close above with 2x volume.
    sig = strategy.on_candle("CIPLA", flat(_dt(20), 103, volume=2500))
    assert sig is not None
    assert sig.action == Action.BUY
    assert sig.symbol == "CIPLA"
    assert sig.stop_price is not None
    assert sig.stop_price < sig.price


def test_no_buy_on_cross_with_low_volume():
    """Cross is rejected when the candle's volume doesn't meet the multiplier."""
    strategy = make_strategy(volume_multiplier=2.0, volume_lookback=3)

    for i in range(3):
        strategy.on_candle("CIPLA", flat(_dt(i * 5), 100, volume=1000))

    strategy.on_candle("CIPLA", flat(_dt(15), 97, volume=1000))   # below VWAP
    # Volume only 1.1x average — below 2.0x threshold.
    sig = strategy.on_candle("CIPLA", flat(_dt(20), 103, volume=1100))
    assert sig is None


def test_exit_on_vwap_recross_below():
    """After a BUY, crossing back below VWAP triggers an EXIT.
    Uses a tight stop_pct (0.1%) so the VWAP-cross exit fires before the
    hard stop when price dips modestly below the VWAP."""
    strategy = make_strategy(volume_multiplier=1.0, stop_pct=0.1)

    for i in range(3):
        strategy.on_candle("CIPLA", flat(_dt(i * 5), 100, volume=1000))

    strategy.on_candle("CIPLA", flat(_dt(15), 97, volume=1000))
    buy_sig = strategy.on_candle("CIPLA", flat(_dt(20), 103, volume=2000))
    assert buy_sig is not None and buy_sig.action == Action.BUY

    # Still above VWAP — no exit.
    sig = strategy.on_candle("CIPLA", flat(_dt(25), 104, volume=1000))
    assert sig is None

    # Modest dip below VWAP (above the 0.1% hard stop) → VWAP-cross EXIT.
    sig = strategy.on_candle("CIPLA", flat(_dt(30), 100, volume=1000))
    assert sig is not None
    assert sig.action == Action.EXIT
    assert "VWAP" in sig.reason


def test_exit_on_hard_stop():
    """Price breaching the hard stop always triggers an EXIT.
    In the common case, a crash also goes below VWAP so the VWAP-cross exit
    fires first (VWAP is the primary exit); in either path an EXIT is emitted.
    This test verifies EXIT happens on a large crash regardless of which check
    fires."""
    strategy = make_strategy(stop_pct=2.0, volume_multiplier=1.0)

    for i in range(3):
        strategy.on_candle("CIPLA", flat(_dt(i * 5), 100, volume=1000))

    strategy.on_candle("CIPLA", flat(_dt(15), 97, volume=1000))
    buy_sig = strategy.on_candle("CIPLA", flat(_dt(20), 100, volume=2000))
    assert buy_sig is not None and buy_sig.action == Action.BUY

    # Gap down well past the 2% stop — must exit.
    crash_price = buy_sig.price * 0.95
    sig = strategy.on_candle("CIPLA", make_candle(_dt(25), crash_price, crash_price, crash_price, crash_price, 500))
    assert sig is not None
    assert sig.action == Action.EXIT


def test_vwap_resets_on_new_day():
    """VWAP accumulator resets at the start of each trading day; a new cross
    on day 2 should generate a fresh BUY rather than carry yesterday's VWAP."""
    strategy = make_strategy(volume_multiplier=1.0, min_candles_after_open=1)
    day1 = datetime(2024, 1, 2, 9, 15)
    day2 = datetime(2024, 1, 3, 9, 15)

    # Day 1: warm up and enter a position.
    for i in range(2):
        strategy.on_candle("CIPLA", flat(day1 + timedelta(minutes=i * 5), 100, volume=1000))
    strategy.on_candle("CIPLA", flat(day1 + timedelta(minutes=10), 95, volume=1000))
    buy1 = strategy.on_candle("CIPLA", flat(day1 + timedelta(minutes=15), 105, volume=2000))
    assert buy1 is not None and buy1.action == Action.BUY

    # Simulate EOD square-off (caller would do this; here manually clear state).
    strategy._state["CIPLA"].position = False
    strategy._state["CIPLA"].stop_price = None

    # Day 2: VWAP should reset.  After enough candles and a fresh cross, a
    # new BUY is generated (not blocked by stale yesterday state).
    for i in range(2):
        strategy.on_candle("CIPLA", flat(day2 + timedelta(minutes=i * 5), 100, volume=1000))
    strategy.on_candle("CIPLA", flat(day2 + timedelta(minutes=10), 95, volume=1000))
    buy2 = strategy.on_candle("CIPLA", flat(day2 + timedelta(minutes=15), 110, volume=2000))
    assert buy2 is not None and buy2.action == Action.BUY
