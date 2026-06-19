from datetime import datetime, timedelta

from strategy.base import Action
from strategy.pairs import PairConfig, PairsStrategy


def make_candle(offset_minutes, close, base):
    return {
        "date": base + timedelta(minutes=offset_minutes),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1000,
    }


def feed_day(strategy, base, prices_a, prices_b):
    """Feeds one candle per symbol per minute, in lockstep, returning every
    non-None signal produced."""
    signals = []
    for i, (price_a, price_b) in enumerate(zip(prices_a, prices_b)):
        candle_a = make_candle(i, price_a, base)
        candle_b = make_candle(i, price_b, base)
        signal = strategy.on_candle("A", candle_a)
        if signal is not None:
            signals.append(signal)
        signal = strategy.on_candle("B", candle_b)
        if signal is not None:
            signals.append(signal)
    return signals


def test_no_signal_before_lookback_fills():
    pair = PairConfig("A", "B", hedge_ratio=1.0)
    strategy = PairsStrategy([pair], spread_lookback=20, entry_z=2.0)
    base = datetime(2024, 1, 1, 9, 15)
    signals = feed_day(strategy, base, [100] * 10, [100] * 10)
    assert signals == []


def test_entry_long_a_short_b_when_spread_drops():
    pair = PairConfig("A", "B", hedge_ratio=1.0)
    strategy = PairsStrategy([pair], spread_lookback=20, entry_z=2.0, exit_z=0.5)
    base = datetime(2024, 1, 1, 9, 15)

    # Stable spread (A == B) for the lookback window, then A drops sharply.
    prices_a = [100] * 20 + [90]
    prices_b = [100] * 20 + [100]
    signals = feed_day(strategy, base, prices_a, prices_b)

    assert len(signals) == 1
    signal = signals[0]
    assert signal.symbol == "A"
    assert signal.action == Action.BUY
    assert signal.leg2_symbol == "B"
    assert signal.leg2_action == Action.SELL
    assert signal.pair_id == "A_B"


def test_entry_short_a_long_b_when_spread_rises():
    pair = PairConfig("A", "B", hedge_ratio=1.0)
    strategy = PairsStrategy([pair], spread_lookback=20, entry_z=2.0, exit_z=0.5)
    base = datetime(2024, 1, 1, 9, 15)

    prices_a = [100] * 20 + [110]
    prices_b = [100] * 20 + [100]
    signals = feed_day(strategy, base, prices_a, prices_b)

    assert len(signals) == 1
    signal = signals[0]
    assert signal.action == Action.SELL
    assert signal.leg2_action == Action.BUY


def test_exit_when_spread_reverts():
    pair = PairConfig("A", "B", hedge_ratio=1.0)
    strategy = PairsStrategy([pair], spread_lookback=20, entry_z=2.0, exit_z=0.5)
    base = datetime(2024, 1, 1, 9, 15)

    prices_a = [100] * 20 + [90] + [100] * 5
    prices_b = [100] * 26
    signals = feed_day(strategy, base, prices_a, prices_b)

    actions = [s.action for s in signals]
    assert Action.BUY in actions
    assert Action.EXIT in actions


def test_unrelated_symbol_produces_no_signal():
    pair = PairConfig("A", "B", hedge_ratio=1.0)
    strategy = PairsStrategy([pair], spread_lookback=20)
    base = datetime(2024, 1, 1, 9, 15)
    signal = strategy.on_candle("C", make_candle(0, 100, base))
    assert signal is None


def test_cancel_pair_allows_reentry_after_failed_sizing():
    pair = PairConfig("A", "B", hedge_ratio=1.0)
    strategy = PairsStrategy([pair], spread_lookback=20, entry_z=2.0, exit_z=0.5)
    base = datetime(2024, 1, 1, 9, 15)

    prices_a = [100] * 20 + [90]
    prices_b = [100] * 21
    signals = feed_day(strategy, base, prices_a, prices_b)
    assert len(signals) == 1  # entry fired, state.position is now set

    # Caller couldn't actually open the position (e.g. sizing rounded to 0).
    strategy.cancel_pair(pair.pair_id)

    # A later candle in the same z-extreme zone can fire another entry,
    # rather than being stuck waiting for a mean-reversion exit that will
    # never come for a position that was never actually opened.
    extra_base = base + timedelta(minutes=len(prices_a) * 5)
    more_signals = feed_day(strategy, extra_base, [90], [100])
    assert len(more_signals) == 1
    assert more_signals[0].action.value == "BUY"
