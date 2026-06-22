"""Runs a backtest for every configured strategy using the current .env
defaults (no sweeping), so you can see at a glance which strategies are
net-positive as currently tuned.

Pairs trading is handled specially: each pair in config/pairs.json is
backtested in isolation (same approach as tune_pairs.py), and a filtered
config -- containing only the pairs that came out net-positive -- is
written to config/pairs_profitable.json. The other strategies (orb,
momentum_halfhour, momentum_swing, breakout_swing) each run once as a whole.

Usage:
    python scripts/run_all_strategies.py
    python scripts/run_all_strategies.py --strategy orb pairs
    python scripts/run_all_strategies.py --pairs-config config/pairs.json

Requires a live Kite session (same as sweep_pairs.py/tune_pairs.py) since
every run fetches real historical candles.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.sweep_pairs import _FIELD_PATTERNS, _REPORT_WRITTEN_RE, RATE_LIMIT_BACKOFF_SECONDS, RATE_LIMIT_RETRIES  # noqa: E402
from scripts.tune_pairs import write_isolated_config  # noqa: E402
from strategy.pairs import load_pairs_config  # noqa: E402

# Strategies that run once as a whole (not isolated per anything).
WHOLE_STRATEGIES = ["orb", "momentum_halfhour", "breakout_swing", "momentum_swing"]
INTER_RUN_DELAY_SECONDS = 5.0
PAIRS_PROFITABLE_OUTPUT = REPO_ROOT / "config" / "pairs_profitable.json"


@dataclass
class RunResult:
    label: str
    net_pnl: float = 0.0
    gross_pnl: float = 0.0
    closed_trades: int = 0
    win_rate: float = 0.0
    total_cost: float = 0.0
    report_path: str = ""
    error: str = ""


def run_backtest(strategy: str, extra_env: dict | None = None) -> RunResult:
    result = RunResult(label=strategy)
    env = os.environ.copy()
    env["MODE"] = "backtest"
    env["STRATEGY"] = strategy
    if extra_env:
        env.update(extra_env)

    proc = None
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        proc = subprocess.run([sys.executable, "main.py"], cwd=REPO_ROOT, env=env, capture_output=True, text=True)
        if proc.returncode == 0 or "Too many requests" not in proc.stderr:
            break
        if attempt < RATE_LIMIT_RETRIES:
            wait = RATE_LIMIT_BACKOFF_SECONDS * (2 ** attempt)
            print(f"      Kite rate limit hit, retrying in {wait:.0f}s...")
            time.sleep(wait)

    if proc.returncode != 0:
        result.error = f"exit code {proc.returncode}: {proc.stderr.strip()[-500:]}"
        return result

    match = _REPORT_WRITTEN_RE.search(proc.stdout)
    if not match:
        result.error = "could not find 'Report written to' line in output"
        return result

    result.report_path = match.group(1).strip()
    try:
        text = Path(result.report_path).read_text()
    except OSError as exc:
        result.error = f"could not read {result.report_path}: {exc}"
        return result

    int_fields = {"closed_trades"}
    for field, pattern in _FIELD_PATTERNS.items():
        m = re.search(pattern, text)
        if m and field in ("closed_trades", "win_rate", "total_cost", "gross_pnl", "net_pnl"):
            value = m.group(1)
            setattr(result, field, int(value) if field in int_fields else float(value))

    return result


def run_pairs_isolated(pairs_config_path: str) -> list[RunResult]:
    pairs = load_pairs_config(pairs_config_path)
    results = []
    for i, pair in enumerate(pairs):
        print(f"  [pairs] [{i + 1}/{len(pairs)}] {pair.pair_id} ...")
        config_path = write_isolated_config(pair)
        result = run_backtest("pairs", extra_env={"PAIRS_CONFIG_PATH": config_path})
        result.label = pair.pair_id
        status = result.error or f"net={result.net_pnl:.2f} ({result.closed_trades} trades)"
        print(f"      -> {status}")
        results.append(result)
        if i < len(pairs) - 1:
            time.sleep(INTER_RUN_DELAY_SECONDS)
    return results


def write_profitable_pairs_config(pairs_config_path: str, results: list[RunResult]) -> list[str]:
    pairs = load_pairs_config(pairs_config_path)
    profitable_ids = {r.label for r in results if not r.error and r.net_pnl > 0}
    keep = [p for p in pairs if p.pair_id in profitable_ids]
    with open(PAIRS_PROFITABLE_OUTPUT, "w") as f:
        json.dump(
            [{"symbol_a": p.symbol_a, "symbol_b": p.symbol_b, "hedge_ratio": p.hedge_ratio} for p in keep], f, indent=2,
        )
    return [p.pair_id for p in keep]


def build_report(whole_results: list[RunResult], pair_results: list[RunResult], kept_pair_ids: list[str]) -> str:
    lines = ["=" * 100, "BACKTEST ACROSS ALL STRATEGIES (current .env defaults)", "=" * 100]
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")

    header = f"{'strategy':<24} {'trades':>7} {'win%':>6} {'cost':>10} {'gross':>12} {'net':>12} {'status':>12}"
    lines.append(header)
    lines.append("-" * len(header))
    for r in whole_results:
        if r.error:
            lines.append(f"{r.label:<24}  ERROR: {r.error}")
            continue
        status = "PROFITABLE" if r.net_pnl > 0 else "net-negative"
        lines.append(
            f"{r.label:<24} {r.closed_trades:>7} {r.win_rate:>6.1f} {r.total_cost:>10.2f} "
            f"{r.gross_pnl:>12.2f} {r.net_pnl:>12.2f} {status:>12}"
        )

    if pair_results:
        lines.append("")
        lines.append("-" * 100)
        lines.append("PAIRS (isolated, current entry_z/exit_z/lookback/capital_pct)")
        lines.append("-" * 100)
        lines.append(header)
        lines.append("-" * len(header))
        for r in pair_results:
            if r.error:
                lines.append(f"{r.label:<24}  ERROR: {r.error}")
                continue
            status = "KEPT" if r.label in kept_pair_ids else "dropped"
            lines.append(
                f"{r.label:<24} {r.closed_trades:>7} {r.win_rate:>6.1f} {r.total_cost:>10.2f} "
                f"{r.gross_pnl:>12.2f} {r.net_pnl:>12.2f} {status:>12}"
            )
        lines.append("")
        lines.append(
            f"{len(kept_pair_ids)}/{len(pair_results)} pairs net-positive -> written to "
            f"{PAIRS_PROFITABLE_OUTPUT.relative_to(REPO_ROOT)}: {kept_pair_ids or '(none)'}"
        )

    lines.append("=" * 100)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strategy", nargs="+", default=None,
        help="Limit to specific strategies (orb, momentum_halfhour, pairs, momentum_swing, breakout_swing). Default: all.",
    )
    parser.add_argument("--pairs-config", default="config/pairs.json", help="Source pairs config to isolate per-pair")
    parser.add_argument("--output", default=None, help="Path for the consolidated report (default: reports/all_strategies_<timestamp>.txt)")
    args = parser.parse_args()

    wanted = set(args.strategy) if args.strategy else set(WHOLE_STRATEGIES + ["pairs"])

    whole_results = []
    to_run = [s for s in WHOLE_STRATEGIES if s in wanted]
    for i, strategy in enumerate(to_run):
        print(f"[{strategy}] running backtest ...")
        result = run_backtest(strategy)
        status = result.error or f"net={result.net_pnl:.2f} ({result.closed_trades} trades)"
        print(f"  -> {status}")
        whole_results.append(result)
        if i < len(to_run) - 1 or "pairs" in wanted:
            time.sleep(INTER_RUN_DELAY_SECONDS)

    pair_results: list[RunResult] = []
    kept_pair_ids: list[str] = []
    if "pairs" in wanted:
        print("[pairs] running isolated per-pair backtests ...")
        pair_results = run_pairs_isolated(args.pairs_config)
        kept_pair_ids = write_profitable_pairs_config(args.pairs_config, pair_results)

    report = build_report(whole_results, pair_results, kept_pair_ids)
    print()
    print(report)

    output_path = Path(args.output) if args.output else REPO_ROOT / "reports" / f"all_strategies_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)
    print(f"\nReport written to {output_path}")


if __name__ == "__main__":
    main()
