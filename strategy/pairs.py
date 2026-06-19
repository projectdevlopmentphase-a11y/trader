"""Pairs trading (statistical arbitrage) strategy.

Trades the spread between two cointegrated stocks: spread = price_a -
hedge_ratio * price_b. Goes long-A/short-B when the spread drops far below
its rolling mean (it's "cheap" relative to B) and short-A/long-B when it
rises far above (it's "rich"), then exits once the spread reverts back
toward the mean. Holds until either the exit threshold or EOD square-off.

Qualifying pairs and hedge ratios come from an offline screener (see
screening/pair_finder.py), since correlation/cointegration only make sense
computed over a long formation window, not maintained tick-by-tick.

Because the underlying on_candle(symbol, candle) interface only ever sees
one symbol at a time, this strategy waits until it has seen both legs'
candles for the same timestamp before evaluating the spread, then returns a
single Signal carrying both legs' order details (see Signal.pair_id et al.
in strategy/base.py) so the two legs are opened/closed as one unit.
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass

from strategy.base import Action, Signal, Strategy


@dataclass
class PairConfig:
    symbol_a: str
    symbol_b: str
    hedge_ratio: float

    @property
    def pair_id(self) -> str:
        return f"{self.symbol_a}_{self.symbol_b}"


def load_pairs_config(path: str) -> list[PairConfig]:
    """Reads the JSON output of screening/pair_finder.py."""
    with open(path) as f:
        raw = json.load(f)
    return [PairConfig(p["symbol_a"], p["symbol_b"], p["hedge_ratio"]) for p in raw]


class _PairState:
    def __init__(self, spread_lookback: int):
        self.spread_history: deque[float] = deque(maxlen=spread_lookback)
        self.last_price: dict[str, float] = {}
        self.last_ts: dict[str, object] = {}
        self.processed_ts: object = None
        # None | "long_a_short_b" | "short_a_long_b"
        self.position: str | None = None


class PairsStrategy(Strategy):
    name = "pairs"

    def __init__(
        self,
        pairs: list[PairConfig],
        spread_lookback: int = 20,
        entry_z: float = 2.0,
        exit_z: float = 0.5,
    ):
        self.spread_lookback = spread_lookback
        self.entry_z = entry_z
        self.exit_z = exit_z
        self._pairs_by_id = {pair.pair_id: pair for pair in pairs}
        self._symbol_to_pair: dict[str, PairConfig] = {}
        for pair in pairs:
            self._symbol_to_pair[pair.symbol_a] = pair
            self._symbol_to_pair[pair.symbol_b] = pair
        self._state: dict[str, _PairState] = {
            pair.pair_id: _PairState(spread_lookback) for pair in pairs
        }
        # Every z-score computed, kept for diagnostics (e.g. tuning entry_z).
        self.z_history: dict[str, list[float]] = {pair.pair_id: [] for pair in pairs}

    def cancel_pair(self, pair_id: str) -> None:
        """Reverts a pending entry that the caller couldn't actually open
        (e.g. position sizing rounded to 0 shares), so the next candle is
        still free to re-evaluate entry instead of being stuck thinking a
        position is already open."""
        state = self._state.get(pair_id)
        if state is not None:
            state.position = None

    def on_candle(self, symbol: str, candle: dict) -> Signal | None:
        pair = self._symbol_to_pair.get(symbol)
        if pair is None:
            return None

        state = self._state[pair.pair_id]
        candle_date = candle["date"]
        state.last_price[symbol] = candle["close"]
        state.last_ts[symbol] = candle_date

        other_symbol = pair.symbol_b if symbol == pair.symbol_a else pair.symbol_a
        if other_symbol not in state.last_price or state.last_ts.get(other_symbol) != candle_date:
            return None
        if state.processed_ts == candle_date:
            return None
        state.processed_ts = candle_date

        price_a = state.last_price[pair.symbol_a]
        price_b = state.last_price[pair.symbol_b]
        spread = price_a - pair.hedge_ratio * price_b
        state.spread_history.append(spread)
        if len(state.spread_history) < self.spread_lookback:
            return None

        mean = sum(state.spread_history) / len(state.spread_history)
        variance = sum((s - mean) ** 2 for s in state.spread_history) / len(state.spread_history)
        std = variance**0.5
        if std == 0:
            return None
        z = (spread - mean) / std
        self.z_history[pair.pair_id].append(z)

        if state.position is None:
            if z <= -self.entry_z:
                state.position = "long_a_short_b"
                return Signal(
                    self.name, pair.symbol_a, Action.BUY, price_a,
                    reason=f"pair entry z={z:.2f}", ts=candle_date,
                    pair_id=pair.pair_id, leg2_symbol=pair.symbol_b,
                    leg2_action=Action.SELL, leg2_price=price_b, hedge_ratio=pair.hedge_ratio,
                )
            if z >= self.entry_z:
                state.position = "short_a_long_b"
                return Signal(
                    self.name, pair.symbol_a, Action.SELL, price_a,
                    reason=f"pair entry z={z:.2f}", ts=candle_date,
                    pair_id=pair.pair_id, leg2_symbol=pair.symbol_b,
                    leg2_action=Action.BUY, leg2_price=price_b, hedge_ratio=pair.hedge_ratio,
                )
            return None

        if abs(z) <= self.exit_z:
            state.position = None
            return Signal(
                self.name, pair.symbol_a, Action.EXIT, price_a,
                reason=f"pair exit z={z:.2f}", ts=candle_date,
                pair_id=pair.pair_id, leg2_symbol=pair.symbol_b,
                leg2_action=Action.EXIT, leg2_price=price_b, hedge_ratio=pair.hedge_ratio,
            )
        return None
