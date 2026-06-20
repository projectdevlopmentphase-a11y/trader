"""Offline pairs screener (statistical arbitrage candidate selection).

Run periodically (e.g. monthly) against same-sector candidate pairs to
decide which are currently tradeable as a stat-arb pair, and what hedge
ratio to use. Cointegration relationships break down over time, so the
output of a screening run is only valid for the trading window that
follows it -- this is not meant to run inside the live/backtest loop.

Pipeline per candidate pair, over a formation window (e.g. 6-12 months of
daily closes):
  1. Price correlation -- drop pairs below MIN_CORRELATION.
  2. Engle-Granger cointegration test (statsmodels) -- keep pairs significant
     at CONFIDENCE_LEVEL (p-value <= 1 - CONFIDENCE_LEVEL).
  3. Hedge ratio via OLS regression of A's price on B's price.

Writes the qualifying pairs + hedge ratios to a JSON file that
strategy/pairs.py reads at startup.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass

if __name__ == "__main__" and __package__ is None:
    # Allow `python screening/pair_finder.py` directly (not just
    # `python -m screening.pair_finder`) by putting the repo root -- this
    # file's parent's parent -- on sys.path so sibling packages like `auth`
    # and `data` are importable.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from statsmodels.tsa.stattools import coint

CANDIDATE_PAIRS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "candidate_pairs.json"
)

MIN_CORRELATION = 0.8
CONFIDENCE_LEVEL = 0.90


def load_candidate_pairs(path: str = CANDIDATE_PAIRS_PATH) -> list[tuple[str, str]]:
    """Reads the candidate symbol-pair list to screen (config/candidate_pairs.json)."""
    with open(path) as f:
        raw = json.load(f)
    return [tuple(pair) for pair in raw]


@dataclass
class PairResult:
    symbol_a: str
    symbol_b: str
    hedge_ratio: float
    correlation: float
    coint_pvalue: float


def compute_correlation(prices_a: list[float], prices_b: list[float]) -> float:
    n = min(len(prices_a), len(prices_b))
    a, b = prices_a[-n:], prices_b[-n:]
    mean_a, mean_b = sum(a) / n, sum(b) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    if var_a <= 0 or var_b <= 0:
        return 0.0
    return cov / (var_a**0.5 * var_b**0.5)


def compute_hedge_ratio(prices_a: list[float], prices_b: list[float]) -> float:
    """OLS slope of A regressed on B: A ~ hedge_ratio * B (+ intercept)."""
    n = min(len(prices_a), len(prices_b))
    a, b = prices_a[-n:], prices_b[-n:]
    mean_a, mean_b = sum(a) / n, sum(b) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    var_b = sum((y - mean_b) ** 2 for y in b)
    if var_b <= 0:
        return 0.0
    return cov / var_b


def run_cointegration_test(prices_a: list[float], prices_b: list[float]) -> float:
    """Engle-Granger cointegration test p-value (lower = more confident the
    pair is cointegrated)."""
    n = min(len(prices_a), len(prices_b))
    _score, pvalue, _crit = coint(prices_a[-n:], prices_b[-n:])
    return pvalue


def screen_pairs(
    candidate_pairs: list[tuple[str, str]],
    price_history: dict[str, list[float]],
    min_correlation: float = MIN_CORRELATION,
    confidence_level: float = CONFIDENCE_LEVEL,
) -> list[PairResult]:
    max_pvalue = 1 - confidence_level
    results: list[PairResult] = []
    for symbol_a, symbol_b in candidate_pairs:
        prices_a = price_history.get(symbol_a)
        prices_b = price_history.get(symbol_b)
        if not prices_a or not prices_b:
            continue

        correlation = compute_correlation(prices_a, prices_b)
        if abs(correlation) < min_correlation:
            continue

        pvalue = run_cointegration_test(prices_a, prices_b)
        if pvalue > max_pvalue:
            continue

        hedge_ratio = compute_hedge_ratio(prices_a, prices_b)
        results.append(PairResult(symbol_a, symbol_b, hedge_ratio, correlation, pvalue))

    return results


def fetch_daily_closes(kite, symbol_to_token: dict[str, int], from_date, to_date) -> dict[str, list[float]]:
    from data.historical import fetch_historical_candles

    closes: dict[str, list[float]] = {}
    for symbol, token in symbol_to_token.items():
        candles = fetch_historical_candles(kite, token, from_date, to_date, "day")
        closes[symbol] = [c["close"] for c in candles]
    return closes


def run_screen(output_path: str, formation_days: int = 365) -> list[PairResult]:
    from datetime import datetime, timedelta

    from auth.kite_auth import KiteAuth

    auth = KiteAuth()
    kite = auth.authenticated_client()

    candidate_pairs = load_candidate_pairs()
    symbols = sorted({symbol for pair in candidate_pairs for symbol in pair})
    instruments = kite.instruments("NSE")
    symbol_to_token = {i["tradingsymbol"]: i["instrument_token"] for i in instruments if i["tradingsymbol"] in symbols}

    to_date = datetime.now()
    from_date = to_date - timedelta(days=formation_days)
    price_history = fetch_daily_closes(kite, symbol_to_token, from_date, to_date)

    results = screen_pairs(candidate_pairs, price_history)

    with open(output_path, "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2)

    return results


if __name__ == "__main__":
    qualifying = run_screen("config/pairs.json")
    for pair in qualifying:
        print(f"{pair.symbol_a}/{pair.symbol_b}: hedge_ratio={pair.hedge_ratio:.4f} "
              f"corr={pair.correlation:.3f} p={pair.coint_pvalue:.4f}")
