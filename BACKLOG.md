# Backlog

Known issues / improvements identified but not yet actioned.

## strategy/pairs.py: shared-symbol pairs break silently

`PairsStrategy.__init__` builds `_symbol_to_pair` as a one-symbol-to-one-pair
dict:

```python
for pair in pairs:
    self._symbol_to_pair[pair.symbol_a] = pair
    self._symbol_to_pair[pair.symbol_b] = pair
```

If a symbol appears in two configured pairs (e.g. `TCS/WIPRO` and
`WIPRO/LTTS` both reference `WIPRO`), the later pair in the list silently
overwrites the mapping for that symbol. The pair that loses the mapping
never receives candles for its shared leg, so it can never match both legs'
timestamps and produces no z-scores/trades -- with no error, just an
empty/quiet diagnostic ("insufficient candle data") that looks like a data
problem rather than a routing bug.

Fix: route a symbol's candles to every pair that references it (e.g.
`_symbol_to_pair: dict[str, list[PairConfig]]`), not just one.

## No aggregate cross-pair capital exposure cap

`risk/risk_manager.py`'s `pairs_position_size()` sizes each pair
independently as `pairs_capital_pct` of capital, with no cap on total
capital deployed across pairs trading concurrently. With multiple
qualifying pairs (now up to 5-6), simultaneous entries across pairs could
deploy far more than 100% of capital in aggregate notional exposure.

## entry_z / exit_z / spread_lookback are global, not per-pair

`PairsStrategy` takes a single `entry_z`, `exit_z`, `spread_lookback` for
all configured pairs. Different pairs have different volatility/spread
characteristics (e.g. MARUTI/M&M profitable at entry_z=2.75/exit_z=0.4/
lookback=400; AXISBANK/SBIN and TCS/WIPRO lose money at the same settings).
Per-pair tunable thresholds would let each pair use its own
backtest-optimal parameters instead of one global setting compromising
across all of them.
