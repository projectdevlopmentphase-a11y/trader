"""Per-pair parameter tuning for the pairs strategy.

scripts/sweep_pairs.py grid-searches PAIRS_ENTRY_Z/PAIRS_EXIT_Z/lookback with
*all* configured pairs trading together, which only tells you how the
portfolio behaves as a whole -- a strong pair's gains can hide a weak pair's
losses (or vice versa). This script isolates each pair from
config/pairs.json one at a time (writing a temporary single-pair config file
and pointing PAIRS_CONFIG_PATH at it), sweeps the same grid against that pair
alone, and reports each pair's own best combination plus its full grid -- so
a pair that needs different thresholds than the rest isn't masked by the
others.

Usage:
    python scripts/tune_pairs.py
    python scripts/tune_pairs.py --entry-z 1.5 2.0 2.5 2.75 3.0 3.5 \
        --exit-z 0.2 0.3 0.4 0.5 0.6 --spread-lookback 100 200 300 400 500 600
    python scripts/tune_pairs.py --pairs-config config/pairs.json --pair-id WIPRO_LTTS

Requires a live Kite session (same as sweep_pairs.py) since each combination
runs the real backtest. Long-running for a full grid x several pairs --
expect it to take a while; it prints progress as it goes.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from itertools import product
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.sweep_pairs import INTER_RUN_DELAY_SECONDS, SweepResult, run_one  # noqa: E402
from strategy.pairs import PairConfig, load_pairs_config  # noqa: E402

TMP_DIR = REPO_ROOT / "reports" / "tune_pairs_tmp"


def write_isolated_config(pair: PairConfig) -> str:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TMP_DIR / f"{pair.pair_id}.json"
    with open(path, "w") as f:
        json.dump([{"symbol_a": pair.symbol_a, "symbol_b": pair.symbol_b, "hedge_ratio": pair.hedge_ratio}], f)
    return str(path)


def tune_pair(
    pair: PairConfig,
    entry_zs: list[float],
    exit_zs: list[float],
    lookbacks: list[int],
    capital_pcts: list[float],
) -> list[SweepResult]:
    config_path = write_isolated_config(pair)
    combos = [
        (entry_z, exit_z, lookback, capital_pct)
        for entry_z, exit_z, lookback, capital_pct in product(entry_zs, exit_zs, lookbacks, capital_pcts)
        if exit_z < entry_z
    ]
    results = []
    for i, (entry_z, exit_z, lookback, capital_pct) in enumerate(combos, 1):
        print(
            f"  [{pair.pair_id}] [{i}/{len(combos)}] entry_z={entry_z} exit_z={exit_z} "
            f"lookback={lookback} capital_pct={capital_pct} ..."
        )
        result = run_one(entry_z, exit_z, lookback, capital_pct, pairs_config_path=config_path)
        status = result.error or f"net={result.net_pnl:.2f} ({result.closed_trades} trades)"
        print(f"      -> {status}")
        results.append(result)
        if i < len(combos):
            time.sleep(INTER_RUN_DELAY_SECONDS)
    return results


def build_pair_section(pair_id: str, results: list[SweepResult]) -> str:
    ranked = sorted(results, key=lambda r: r.net_pnl, reverse=True)
    lines = ["-" * 100, f"PAIR: {pair_id}", "-" * 100]
    header = f"{'entry_z':>8} {'exit_z':>7} {'lookback':>9} {'cap_pct':>8} {'trades':>7} {'win%':>6} {'cost':>10} {'gross':>12} {'net':>12}"
    lines.append(header)
    lines.append("-" * len(header))
    for r in ranked:
        if r.error:
            lines.append(f"{r.entry_z:>8} {r.exit_z:>7} {r.spread_lookback:>9} {r.capital_pct:>8}  ERROR: {r.error}")
            continue
        lines.append(
            f"{r.entry_z:>8} {r.exit_z:>7} {r.spread_lookback:>9} {r.capital_pct:>8} "
            f"{r.closed_trades:>7} {r.win_rate:>6.1f} {r.total_cost:>10.2f} {r.gross_pnl:>12.2f} {r.net_pnl:>12.2f}"
        )
    valid = [r for r in ranked if not r.error]
    if valid:
        best = valid[0]
        profitable = [r for r in valid if r.net_pnl > 0]
        lines.append("")
        lines.append(
            f"Best for {pair_id}: entry_z={best.entry_z} exit_z={best.exit_z} "
            f"lookback={best.spread_lookback} capital_pct={best.capital_pct} "
            f"-> net={best.net_pnl:.2f} ({best.closed_trades} trades, {best.win_rate:.1f}% win rate)"
        )
        lines.append(f"{len(profitable)}/{len(valid)} combinations net-profitable for this pair.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs-config", default="config/pairs.json", help="Source pairs config to tune (each pair isolated in turn)")
    parser.add_argument("--pair-id", action="append", default=None, help="Limit tuning to specific pair_id(s) (symbol_a_symbol_b). Repeatable. Default: all pairs.")
    parser.add_argument("--entry-z", type=float, nargs="+", default=[1.5, 2.0, 2.5, 2.75, 3.0, 3.5])
    parser.add_argument("--exit-z", type=float, nargs="+", default=[0.2, 0.3, 0.4, 0.5, 0.6])
    parser.add_argument("--spread-lookback", type=int, nargs="+", default=[100, 200, 300, 400, 500, 600])
    parser.add_argument("--capital-pct", type=float, nargs="+", default=[40.0])
    parser.add_argument("--output", default=None, help="Path for the consolidated tuning report (default: reports/tune_pairs_<timestamp>.txt)")
    args = parser.parse_args()

    pairs = load_pairs_config(args.pairs_config)
    if args.pair_id:
        wanted = set(args.pair_id)
        pairs = [p for p in pairs if p.pair_id in wanted]
        missing = wanted - {p.pair_id for p in pairs}
        if missing:
            print(f"Warning: pair_id(s) not found in {args.pairs_config}: {sorted(missing)}")

    if not pairs:
        print("No pairs to tune.")
        return

    print(f"Tuning {len(pairs)} pair(s) independently: {[p.pair_id for p in pairs]}")

    sections = []
    overall_best = []
    for pair in pairs:
        results = tune_pair(pair, args.entry_z, args.exit_z, args.spread_lookback, args.capital_pct)
        sections.append(build_pair_section(pair.pair_id, results))
        valid = [r for r in results if not r.error]
        if valid:
            best = max(valid, key=lambda r: r.net_pnl)
            overall_best.append((pair.pair_id, best))

    summary_lines = ["=" * 100, "PER-PAIR TUNING SUMMARY", "=" * 100]
    summary_lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    summary_lines.append(f"Pairs tuned: {len(pairs)}")
    summary_lines.append("")
    for pair_id, best in overall_best:
        verdict = "PROFITABLE" if best.net_pnl > 0 else "still net-negative"
        summary_lines.append(
            f"  {pair_id:<24} best: entry_z={best.entry_z} exit_z={best.exit_z} "
            f"lookback={best.spread_lookback} -> net={best.net_pnl:.2f} ({verdict})"
        )
    summary_lines.append("=" * 100)
    summary_lines.append("")

    report = "\n".join(summary_lines) + "\n" + "\n".join(sections)
    print()
    print(report)

    output_path = Path(args.output) if args.output else REPO_ROOT / "reports" / f"tune_pairs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)
    print(f"\nTuning report written to {output_path}")


if __name__ == "__main__":
    main()
