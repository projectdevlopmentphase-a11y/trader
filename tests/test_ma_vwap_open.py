from datetime import datetime, timedelta

from strategy.base import Action
from strategy.ma_vwap_open import MAVWAPOpenStrategy


def _dt(h: int, m: int, day: int = 2) -> datetime:
    return datetime(2024, 1, day, h, m)


def candle(ts: datetime, close: float, volume: float = 10_000, high=None, low=None) -> dict:
    h = high if high is not None else close
    l = low if low is not None else close
    return {"date": ts, "open": close, "high": h, "low": l, "close": close, "volume": volume}


def make_strategy(**kw):
    defaults = dict(ma_fast=3, ma_slow=5, stop_pct=1.0)
    defaults.update(kw)
    return MAVWAPOpenStrategy(**defaults)


def _warm_up(strategy, symbol, n, base_ts, price=100.0, above_vwap=True):
    """Feed n candles of flat price to build MA history."""
    for i in range(n):
        strategy.on_candle(symbol, candle(base_ts + timedelta(minutes=i), price))


def test_no_entry_before_enough_ma_history():
    s = make_strategy()   # ma_slow=5
    for i in range(4):    # only 4 candles, need 5
        sig = s.on_candle("A", candle(_dt(9, 15 + i), 105.0))
    assert sig is None


def test_no_entry_outside_time_window():
    """Signal must not fire after 09:45 even if all other conditions hold."""
    s = make_strategy()
    # Warm up 5 candles inside window.
    for i in range(5):
        s.on_candle("A", candle(_dt(9, 15 + i), 100.0))
    # Feed a qualifying candle at 09:50 (outside window).
    sig = s.on_candle("A", candle(_dt(9, 50), 110.0, volume=50_000))
    assert sig is None or sig.action != Action.BUY


def test_entry_when_all_conditions_met():
    """BUY fires inside the window when MA9>MA21 and close>VWAP."""
    s = make_strategy(ma_fast=2, ma_slow=3)
    # Feed 3 rising candles starting at 9:15 → MA fast > MA slow, VWAP~100.
    prices = [99.0, 100.0, 102.0]
    for i, p in enumerate(prices):
        sig = s.on_candle("A", candle(_dt(9, 15 + i), p))
    # Third candle at 9:17 should have MA2(101) > MA3(100.33) and close(102)>VWAP.
    assert sig is not None
    assert sig.action == Action.BUY
    assert sig.stop_price is not None and sig.stop_price < sig.price


def test_no_second_entry_same_day():
    """Once in a position, no second BUY fires on the same day."""
    s = make_strategy(ma_fast=2, ma_slow=3)
    prices = [99.0, 100.0, 102.0]
    buy_sig = None
    for i, p in enumerate(prices):
        buy_sig = s.on_candle("A", candle(_dt(9, 15 + i), p))
    assert buy_sig is not None and buy_sig.action == Action.BUY

    # Another qualifying candle — must not re-enter.
    sig = s.on_candle("A", candle(_dt(9, 20), 105.0, volume=50_000))
    assert sig is None or sig.action != Action.BUY


def test_exit_on_vwap_cross_back():
    """Position is closed when close drops below VWAP."""
    s = make_strategy(ma_fast=2, ma_slow=3, stop_pct=20.0)  # wide stop to isolate VWAP exit
    for i, p in enumerate([99.0, 100.0, 102.0]):
        sig = s.on_candle("A", candle(_dt(9, 15 + i), p))
    assert sig is not None and sig.action == Action.BUY

    # Big dip far below VWAP (current VWAP ~100.33) and below MA.
    sig = s.on_candle("A", candle(_dt(9, 18), 85.0))
    assert sig is not None
    assert sig.action == Action.EXIT
    assert "VWAP" in sig.reason


def test_exit_on_ma_cross():
    """Position is closed when MA9 crosses below MA21."""
    s = make_strategy(ma_fast=2, ma_slow=3, stop_pct=50.0)
    for i, p in enumerate([99.0, 100.0, 102.0]):
        s.on_candle("A", candle(_dt(9, 15 + i), p))

    # Small dip that doesn't breach VWAP but drags MA fast below MA slow.
    # VWAP ~100.33; close at 100.5 is above it but MA changes.
    # Feed two falling candles: 102 -> 100.5 -> 99.0
    s.on_candle("A", candle(_dt(9, 18), 100.5))
    sig = s.on_candle("A", candle(_dt(9, 19), 99.0))
    assert sig is not None
    assert sig.action == Action.EXIT


def test_force_exit_squares_open_position():
    """force_exit closes the position and returns an EXIT signal."""
    s = make_strategy(ma_fast=2, ma_slow=3)
    for i, p in enumerate([99.0, 100.0, 102.0]):
        s.on_candle("A", candle(_dt(9, 15 + i), p))

    sig = s.force_exit("A", 103.0, _dt(15, 20))
    assert sig is not None
    assert sig.action == Action.EXIT
    assert "EOD" in sig.reason
    # Second call — position already closed, no signal.
    assert s.force_exit("A", 103.0, _dt(15, 20)) is None


def test_vwap_resets_on_new_day():
    """Accumulators reset at midnight; a fresh BUY can fire on day 2."""
    s = make_strategy(ma_fast=2, ma_slow=3)
    # Day 1: enter and close position.
    for i, p in enumerate([99.0, 100.0, 102.0]):
        s.on_candle("A", candle(_dt(9, 15 + i, day=2), p))
    s.force_exit("A", 102.0, _dt(15, 20, day=2))

    # Day 2: fresh candles, should get a new BUY.
    sigs = []
    for i, p in enumerate([99.0, 100.0, 103.0]):
        sigs.append(s.on_candle("A", candle(_dt(9, 15 + i, day=3), p)))
    buys = [sg for sg in sigs if sg is not None and sg.action == Action.BUY]
    assert len(buys) == 1
