"""Grid-search PAIRS_ENTRY_Z / PAIRS_EXIT_Z (and optionally spread_lookback /
capital_pct) by running the real pairs backtest once per combination and
collating the results into a single sweep report.

Each combination runs `python main.py` in a subprocess with MODE=backtest,
STRATEGY=pairs and the swept variables set via env vars -- exactly the same
path a manual `PAIRS_ENTRY_Z=2.5 PAIRS_EXIT_Z=0.3 python main.py` run takes,
just looped and the resulting reports/<run>/report.txt files parsed and
ranked by net P&L (after the transaction-cost + slippage model) instead of
gross P&L.

Usage:
    python scripts/sweep_pairs.py
    python scripts/sweep_pairs.py --entry-z 2.0 2.25 2.5 2.75 3.0 --exit-z 0.3 0.4 0.5
    python scripts/sweep_pairs.py --capital-pct 10 40 --spread-lookback 20 30

Requires a live Kite session (KITE_API_KEY/SECRET/ACCESS_TOKEN in .env) since
the backtest fetches real historical candles -- this is meant to run on the
machine that already runs `python main.py` for pairs backtests, not in CI.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from itertools import product
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_REPORT_WRITTEN_RE = re.compile(r"Report written to (.+)$", re.MULTILINE)
_FIELD_PATTERNS = {
    "legs": r"Total trade legs \(entries \+ exits\): (\d+)",
    "closed_trades": r"Closed round trips: (\d+)",
    "win_rate": r"Win rate: ([\d.]+)%",
    "total_cost": r"Total transaction costs \([^)]*\): (-?[\d.]+)",
    "gross_pnl": r"Gross P&L: (-?[\d.]+)",
    "net_pnl": r"Net P&L: (-?[\d.]+)",
}


@dataclass
class SweepResult:
    entry_z: float
    exit_z: float
    spread_lookback: int
    capital_pct: float
    legs: int = 0
    closed_trades: int = 0
    win_rate: float = 0.0
    total_cost: float = 0.0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    report_path: str = ""
    error: str = ""

    @property
    def avg_net_per_trade(self) -> float:
        return self.net_pnl / self.closed_trades if self.closed_trades else 0.0


def run_one(
    entry_z: float, exit_z: float, spread_lookback: int, capital_pct: float,
    pairs_config_path: str | None = None,
) -> SweepResult:
    result = SweepResult(entry_z, exit_z, spread_lookback, capital_pct)

    env = os.environ.copy()
    env["MODE"] = "backtest"
    env["STRATEGY"] = "pairs"
    env["PAIRS_ENTRY_Z"] = str(entry_z)
    env["PAIRS_EXIT_Z"] = str(exit_z)
    env["PAIRS_SPREAD_LOOKBACK"] = str(spread_lookback)
    env["PAIRS_CAPITAL_PCT"] = str(capital_pct)
    if pairs_config_path is not None:
        env["PAIRS_CONFIG_PATH"] = pairs_config_path

    proc = subprocess.run(
        [sys.executable, "main.py"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        result.error = f"exit code {proc.returncode}: {proc.stderr.strip()[-500:]}"
        return result

    match = _REPORT_WRITTEN_RE.search(proc.stdout)
    if not match:
        result.error = "could not find 'Report written to' line in output"
        return result

    report_path = match.group(1).strip()
    result.report_path = report_path
    try:
        text = Path(report_path).read_text()
    except OSError as exc:
        result.error = f"could not read {report_path}: {exc}"
        return result

    int_fields = {"legs", "closed_trades"}
    for field, pattern in _FIELD_PATTERNS.items():
        m = re.search(pattern, text)
        if m:
            value = m.group(1)
            setattr(result, field, int(value) if field in int_fields else float(value))

    return result


def build_summary(results: list[SweepResult]) -> str:
    ranked = sorted(results, key=lambda r: r.net_pnl, reverse=True)

    lines = ["=" * 100, "PAIRS STRATEGY PARAMETER SWEEP", "=" * 100]
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Combinations run: {len(results)}")
    lines.append("")
    header = f"{'entry_z':>8} {'exit_z':>7} {'lookback':>9} {'cap_pct':>8} {'legs':>6} {'trades':>7} {'win%':>6} {'cost':>10} {'gross':>12} {'net':>12} {'net/trade':>10}"
    lines.append(header)
    lines.append("-" * len(header))
    for r in ranked:
        if r.error:
            lines.append(f"{r.entry_z:>8} {r.exit_z:>7} {r.spread_lookback:>9} {r.capital_pct:>8}  ERROR: {r.error}")
            continue
        lines.append(
            f"{r.entry_z:>8} {r.exit_z:>7} {r.spread_lookback:>9} {r.capital_pct:>8} "
            f"{r.legs:>6} {r.closed_trades:>7} {r.win_rate:>6.1f} {r.total_cost:>10.2f} "
            f"{r.gross_pnl:>12.2f} {r.net_pnl:>12.2f} {r.avg_net_per_trade:>10.2f}"
        )
    lines.append("-" * len(header))

    valid = [r for r in ranked if not r.error]
    if valid:
        best = valid[0]
        profitable = [r for r in valid if r.net_pnl > 0]
        lines.append("")
        lines.append(
            f"Best net P&L: entry_z={best.entry_z} exit_z={best.exit_z} "
            f"lookback={best.spread_lookback} capital_pct={best.capital_pct} "
            f"-> net={best.net_pnl:.2f} ({best.closed_trades} trades, {best.win_rate:.1f}% win rate)"
        )
        if profitable:
            lines.append(f"{len(profitable)}/{len(valid)} combinations were net-profitable.")
        else:
            lines.append(
                f"0/{len(valid)} combinations were net-profitable -- every tested combination "
                "lost money after transaction costs and slippage. Widening/narrowing entry_z "
                "and exit_z alone does not fix this; consider re-screening for cointegration "
                "(screening/pair_finder.py) before further threshold tuning."
            )

    lines.append("=" * 100)
    for r in ranked:
        if r.report_path:
            lines.append(f"  entry_z={r.entry_z} exit_z={r.exit_z}: {r.report_path}")
    lines.append("=" * 100)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry-z", type=float, nargs="+", default=[2.0, 2.25, 2.5, 2.75, 3.0])
    parser.add_argument("--exit-z", type=float, nargs="+", default=[0.3, 0.4, 0.5])
    parser.add_argument("--spread-lookback", type=int, nargs="+", default=[20])
    parser.add_argument("--capital-pct", type=float, nargs="+", default=[40.0])
    parser.add_argument(
        "--output", default=None,
        help="Path for the consolidated sweep report (default: reports/sweep_<timestamp>.txt)",
    )
    args = parser.parse_args()

    combos = [
        (entry_z, exit_z, lookback, capital_pct)
        for entry_z, exit_z, lookback, capital_pct in product(
            args.entry_z, args.exit_z, args.spread_lookback, args.capital_pct
        )
        if exit_z < entry_z  # exit threshold must be tighter than entry
    ]

    print(f"Running {len(combos)} combinations...")
    results = []
    for i, (entry_z, exit_z, lookback, capital_pct) in enumerate(combos, 1):
        print(f"[{i}/{len(combos)}] entry_z={entry_z} exit_z={exit_z} lookback={lookback} capital_pct={capital_pct} ...")
        result = run_one(entry_z, exit_z, lookback, capital_pct)
        status = result.error or f"net={result.net_pnl:.2f}"
        print(f"    -> {status}")
        results.append(result)

    summary = build_summary(results)
    print()
    print(summary)

    output_path = Path(args.output) if args.output else REPO_ROOT / "reports" / f"sweep_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(summary)
    print(f"\nSweep summary written to {output_path}")


if __name__ == "__main__":
    main()
