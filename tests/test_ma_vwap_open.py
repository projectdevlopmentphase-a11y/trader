from datetime import datetime, timedelta

from strategy.base import Action
from strategy.ma_vwap_open import MAVWAPOpenStrategy


def _dt(h: int, m: int, day: int = 2) -> datetime:
    return datetime(2024, 1, day, h, m)


def candle(ts: datetime, close: float, volume: float = 10_000, high=None, low=None) -> dict:
    h = high if high is not None else close
    l = low if low is not None else close
    return {"date": ts, "open": close, "high": h, "low": l, "close": close, "volume": volume}


def _rising(start_dt, prices, vol=10_000):
    return [candle(start_dt + timedelta(minutes=i), p, vol) for i, p in enumerate(prices)]


# ── Helpers ─────────────────────────────────────────────────────────────────

def _feed(strategy, symbol, candles):
    sigs = []
    for c in candles:
        sigs.append(strategy.on_candle(symbol, c))
    return sigs


def _enter(strategy, symbol, day=2) -> object:
    """Feed enough rising candles to trigger a BUY."""
    prices = [99.0, 100.0, 102.0]
    sigs = []
    for i, p in enumerate(prices):
        sigs.append(strategy.on_candle(symbol, candle(_dt(9, 15 + i, day=day), p)))
    buys = [s for s in sigs if s is not None and s.action == Action.BUY]
    return buys[0] if buys else None


# ── Mode-independent tests ───────────────────────────────────────────────────

def test_no_entry_before_enough_ma_history():
    s = MAVWAPOpenStrategy(ma_fast=3, ma_slow=5)
    for i in range(4):
        sig = s.on_candle("A", candle(_dt(9, 15 + i), 105.0))
    assert sig is None


def test_no_entry_outside_time_window():
    s = MAVWAPOpenStrategy(ma_fast=2, ma_slow=3)
    for i in range(3):
        s.on_candle("A", candle(_dt(9, 15 + i), 100.0))
    sig = s.on_candle("A", candle(_dt(9, 50), 110.0, volume=50_000))
    assert sig is None or sig.action != Action.BUY


def test_no_second_entry_same_day():
    s = MAVWAPOpenStrategy(ma_fast=2, ma_slow=3)
    buy = _enter(s, "A")
    assert buy is not None and buy.action == Action.BUY
    sig = s.on_candle("A", candle(_dt(9, 20), 105.0, volume=50_000))
    assert sig is None or sig.action != Action.BUY


def test_force_exit_squares_open_position():
    s = MAVWAPOpenStrategy(ma_fast=2, ma_slow=3)
    _enter(s, "A")
    sig = s.force_exit("A", 103.0, _dt(15, 20))
    assert sig is not None and sig.action == Action.EXIT and "EOD" in sig.reason
    assert s.force_exit("A", 103.0, _dt(15, 20)) is None


def test_vwap_resets_on_new_day():
    s = MAVWAPOpenStrategy(ma_fast=2, ma_slow=3)
    _enter(s, "A", day=2)
    s.force_exit("A", 102.0, _dt(15, 20, day=2))
    buy2 = _enter(s, "A", day=3)
    assert buy2 is not None and buy2.action == Action.BUY


# ── Mode: "vwap" ─────────────────────────────────────────────────────────────

def test_vwap_exit_on_vwap_recross():
    s = MAVWAPOpenStrategy(ma_fast=2, ma_slow=3, stop_pct=20.0, exit_mode="vwap")
    _enter(s, "A")
    sig = s.on_candle("A", candle(_dt(9, 18), 85.0))
    assert sig is not None and sig.action == Action.EXIT and "VWAP" in sig.reason


def test_vwap_exit_on_ma_cross():
    s = MAVWAPOpenStrategy(ma_fast=2, ma_slow=3, stop_pct=50.0, exit_mode="vwap")
    _enter(s, "A")
    s.on_candle("A", candle(_dt(9, 18), 100.5))
    sig = s.on_candle("A", candle(_dt(9, 19), 99.0))
    assert sig is not None and sig.action == Action.EXIT


# ── Mode: "target" ────────────────────────────────────────────────────────────

def test_target_exit_on_profit_target():
    """BUY at ~102, stop at ~102*(1-1%)=100.98, dist≈1.02, 2R target≈104.04."""
    s = MAVWAPOpenStrategy(ma_fast=2, ma_slow=3, stop_pct=1.0, exit_mode="target", target_r=2.0)
    buy = _enter(s, "A")
    assert buy is not None
    # Entry ~102; dist ≈ 1.02; target ≈ 104.04
    target = buy.price + 2.0 * (buy.price - buy.stop_price)
    sig = s.on_candle("A", candle(_dt(9, 18), target + 1))
    assert sig is not None and sig.action == Action.EXIT and "target" in sig.reason


def test_target_no_exit_on_vwap_recross():
    """In target mode, crossing back below VWAP should NOT trigger an exit."""
    s = MAVWAPOpenStrategy(ma_fast=2, ma_slow=3, stop_pct=10.0, exit_mode="target", target_r=5.0)
    _enter(s, "A")
    # Price dips to 98 — below early VWAP (~100) but well above 10% stop.
    sig = s.on_candle("A", candle(_dt(9, 18), 98.0))
    # Should not exit (no VWAP recross exit in target mode).
    assert sig is None or sig.action != Action.EXIT


def test_target_exit_on_stop():
    s = MAVWAPOpenStrategy(ma_fast=2, ma_slow=3, stop_pct=1.0, exit_mode="target", target_r=10.0)
    buy = _enter(s, "A")
    assert buy is not None
    crash = buy.stop_price - 1
    sig = s.on_candle("A", candle(_dt(9, 18), crash))
    assert sig is not None and sig.action == Action.EXIT and "stop" in sig.reason


# ── Mode: "prev_vwap" ─────────────────────────────────────────────────────────

def test_prev_vwap_no_entry_on_first_day():
    """On the very first day there is no prev_vwap yet — no entry should fire."""
    s = MAVWAPOpenStrategy(ma_fast=2, ma_slow=3, exit_mode="prev_vwap")
    buys = [sig for sig in _feed(s, "A", _rising(_dt(9, 15), [99, 100, 103]))
            if sig is not None and sig.action == Action.BUY]
    assert buys == []


def test_prev_vwap_entry_uses_previous_day_level():
    """After day 1, prev_vwap is set; a strong open on day 2 should trigger entry."""
    s = MAVWAPOpenStrategy(ma_fast=2, ma_slow=3, stop_pct=1.0, exit_mode="prev_vwap")
    # Day 1: feed candles, let VWAP accumulate (close ~100 → prev_vwap ~100).
    _feed(s, "A", _rising(_dt(9, 15, day=2), [100, 100, 100, 100]))
    # Day 2: open strongly above prev_vwap with rising MAs.
    sigs = _feed(s, "A", _rising(_dt(9, 15, day=3), [99, 100, 103]))
    buys = [sg for sg in sigs if sg is not None and sg.action == Action.BUY]
    assert len(buys) == 1


def test_prev_vwap_exit_below_prev_vwap():
    s = MAVWAPOpenStrategy(ma_fast=2, ma_slow=3, stop_pct=50.0, exit_mode="prev_vwap")
    # Day 1: VWAP ~100.
    _feed(s, "A", _rising(_dt(9, 15, day=2), [100, 100, 100, 100]))
    # Day 2: enter.
    _feed(s, "A", _rising(_dt(9, 15, day=3), [99, 100, 103]))
    # Drop below prev_vwap (~100).
    sig = s.on_candle("A", candle(_dt(9, 18, day=3), 95.0))
    assert sig is not None and sig.action == Action.EXIT and "prev-day VWAP" in sig.reason
