"""Backtest: MA9 > MA21 + VWAP filter, 1-minute candles, 09:15–09:45 entry
window, all current Nifty 50 stocks, past 6 months.

Runs three exit-mode variants back-to-back and prints a comparison table:
  vwap      — exit when close falls back below today's session VWAP (original)
  target    — fixed 2R profit target; no VWAP recross exit
  prev_vwap — entry filter uses previous day's closing VWAP; exit on
               close < prev_vwap or MA cross

Memory-efficient: fetches, replays, and frees one symbol at a time.

Run:
    python scripts/backtest_ma_vwap_open.py
    python scripts/backtest_ma_vwap_open.py --months 3 --stop-pct 1.5
    python scripts/backtest_ma_vwap_open.py --symbols RELIANCE TCS INFY
    python scripts/backtest_ma_vwap_open.py --mode target   # single mode only
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from auth.kite_auth import KiteAuth  # noqa: E402
from data.historical import fetch_historical_candles, fetch_instruments  # noqa: E402
from execution.costs import apply_slippage, calculate_transaction_cost  # noqa: E402
from strategy.ma_vwap_open import MAVWAPOpenStrategy  # noqa: E402

NIFTY_50 = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "SBIN", "LT", "ITC", "AXISBANK", "KOTAKBANK",
    "BAJFINANCE", "BHARTIARTL", "MARUTI", "ASIANPAINT", "TITAN",
    "SUNPHARMA", "ULTRACEMCO", "WIPRO", "HCLTECH", "BAJAJ-AUTO",
    "MM", "TECHM", "POWERGRID", "NTPC", "ONGC",
    "TATAMOTORS", "TATASTEEL", "JSWSTEEL", "BPCL", "COALINDIA",
    "GRASIM", "NESTLEIND", "CIPLA", "DRREDDY", "DIVISLAB",
    "ADANIENT", "ADANIPORTS", "INDUSINDBK", "EICHERMOT", "APOLLOHOSP",
    "BRITANNIA", "HEROMOTOCO", "HINDALCO", "SHRIRAMFIN", "TRENT",
    "BEL", "ZOMATO", "BAJAJFINSV", "LTIM",
]

_SQUARE_OFF  = dt_time(15, 20)
_SLIPPAGE_BPS = 5.0
_CAPITAL      = 100_000.0
_RISK_PCT     = 1.0
_OUTPUT_DIR   = REPO_ROOT / "reports"
_TARGET_R     = 2.0   # reward multiple for "target" mode


def position_size(entry: float, stop: float) -> int:
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return 0
    return max(int((_CAPITAL * _RISK_PCT / 100) / risk_per_share), 1)


def replay_symbol(symbol: str, candles: list[dict], strategy: MAVWAPOpenStrategy, stop_pct: float) -> list[dict]:
    """Replay one symbol's sorted candles; return trade records."""
    trades: list[dict] = []
    open_pos: dict | None = None

    by_day: dict[str, list[dict]] = defaultdict(list)
    for c in candles:
        ts  = c["date"]
        day = ts.date().isoformat() if isinstance(ts, datetime) else str(ts)[:10]
        by_day[day].append(c)

    for day in sorted(by_day):
        last_candle: dict | None = None

        for candle in by_day[day]:
            ts = candle["date"]
            if not isinstance(ts, datetime) or ts.time() > _SQUARE_OFF:
                break
            last_candle = candle

            sig = strategy.on_candle(symbol, candle)
            if sig is None:
                continue

            if sig.action.value == "BUY" and open_pos is None:
                fill  = apply_slippage("BUY", sig.price, _SLIPPAGE_BPS)
                stop  = sig.stop_price or fill * (1 - stop_pct / 100)
                qty   = position_size(fill, stop)
                cost  = calculate_transaction_cost("BUY", fill, qty, "MIS")
                open_pos = {"price": fill, "cost": cost, "qty": qty, "ts": ts,
                            "day": day, "reason": sig.reason}

            elif sig.action.value == "EXIT" and open_pos is not None:
                fill      = apply_slippage("SELL", sig.price, _SLIPPAGE_BPS)
                exit_cost = calculate_transaction_cost("SELL", fill, open_pos["qty"], "MIS")
                gross     = (fill - open_pos["price"]) * open_pos["qty"]
                total_cost = open_pos["cost"] + exit_cost
                trades.append(_row(symbol, open_pos, fill, ts, gross, total_cost, sig.reason))
                open_pos = None

        # EOD square-off.
        if open_pos is not None and last_candle is not None:
            sig = strategy.force_exit(symbol, last_candle["close"], last_candle["date"])
            if sig:
                fill      = apply_slippage("SELL", last_candle["close"], _SLIPPAGE_BPS)
                exit_cost = calculate_transaction_cost("SELL", fill, open_pos["qty"], "MIS")
                gross     = (fill - open_pos["price"]) * open_pos["qty"]
                total_cost = open_pos["cost"] + exit_cost
                trades.append(_row(symbol, open_pos, fill, last_candle["date"],
                                   gross, total_cost, "EOD square-off"))
                open_pos = None

    return trades


def _row(symbol, pos, exit_price, exit_ts, gross, total_cost, exit_reason) -> dict:
    return {
        "symbol":       symbol,
        "day":          pos["day"],
        "entry_ts":     pos["ts"].isoformat(),
        "exit_ts":      exit_ts.isoformat() if isinstance(exit_ts, datetime) else str(exit_ts),
        "entry_price":  round(pos["price"], 2),
        "exit_price":   round(exit_price, 2),
        "qty":          pos["qty"],
        "gross_pnl":    round(gross, 2),
        "cost":         round(total_cost, 2),
        "net_pnl":      round(gross - total_cost, 2),
        "exit_reason":  exit_reason,
        "entry_reason": pos["reason"],
    }


def run_variant(
    mode: str,
    universe: list[str],
    symbol_to_token: dict,
    kite,
    from_date: datetime,
    to_date: datetime,
    stop_pct: float,
    candle_cache: dict[str, list[dict]],
) -> list[dict]:
    """Run one exit-mode variant.  Candles are cached so all three variants
    share the same fetched data without re-hitting the API."""
    print(f"\n── Mode: {mode.upper()} {'─'*40}")
    all_trades: list[dict] = []
    missing: list[str] = []

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = _OUTPUT_DIR / f"ma_vwap_open_{mode}_trades.csv"
    csv_file = open(csv_path, "w", newline="")
    csv_writer: csv.DictWriter | None = None

    for idx, symbol in enumerate(universe, 1):
        token = symbol_to_token.get(symbol)
        if token is None:
            missing.append(symbol)
            print(f"  [{idx:>2}/{len(universe)}] {symbol:<14} SKIP")
            continue

        # Fetch once; reuse for subsequent modes.
        if symbol not in candle_cache:
            candle_cache[symbol] = fetch_historical_candles(kite, token, from_date, to_date, "minute")

        candles  = candle_cache[symbol]
        strategy = MAVWAPOpenStrategy(
            ma_fast=9, ma_slow=21, stop_pct=stop_pct,
            exit_mode=mode, target_r=_TARGET_R,
        )
        trades   = replay_symbol(symbol, candles, strategy, stop_pct)

        sym_net  = sum(t["net_pnl"] for t in trades)
        sym_wins = sum(1 for t in trades if t["net_pnl"] > 0)
        print(f"  [{idx:>2}/{len(universe)}] {symbol:<14} {len(candles):>7} candles  "
              f"{len(trades):>3} trades  wins={sym_wins}  net=₹{sym_net:,.2f}")

        if trades:
            if csv_writer is None:
                csv_writer = csv.DictWriter(csv_file, fieldnames=trades[0].keys())
                csv_writer.writeheader()
            csv_writer.writerows(trades)
            csv_file.flush()

        all_trades.extend(trades)

    csv_file.close()
    if missing:
        print(f"  [SKIP] unrecognised: {', '.join(missing)}")
    return all_trades


def _summary(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "wins": 0, "win_rate": 0.0,
                "gross": 0.0, "cost": 0.0, "net": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0}
    wins   = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    return {
        "trades":   len(trades),
        "wins":     len(wins),
        "win_rate": len(wins) / len(trades) * 100,
        "gross":    sum(t["gross_pnl"] for t in trades),
        "cost":     sum(t["cost"] for t in trades),
        "net":      sum(t["net_pnl"] for t in trades),
        "avg_win":  sum(t["net_pnl"] for t in wins)  / len(wins)   if wins   else 0.0,
        "avg_loss": sum(t["net_pnl"] for t in losses) / len(losses) if losses else 0.0,
    }


def _print_comparison(results: dict[str, list[dict]]) -> None:
    print("\n" + "=" * 70)
    print("COMPARISON: MA9>MA21 + VWAP filter  |  1-min  |  09:15–09:45")
    print("=" * 70)
    fmt = "{:<14} {:>7} {:>6} {:>8} {:>12} {:>12} {:>10} {:>10}"
    print(fmt.format("Mode", "Trades", "Win%", "Costs", "Gross P&L", "Net P&L", "Avg win", "Avg loss"))
    print("-" * 70)
    for mode, trades in results.items():
        s = _summary(trades)
        print(fmt.format(
            mode,
            s["trades"],
            f"{s['win_rate']:.1f}%",
            f"₹{s['cost']:,.0f}",
            f"₹{s['gross']:,.0f}",
            f"₹{s['net']:,.0f}",
            f"₹{s['avg_win']:,.0f}",
            f"₹{s['avg_loss']:,.0f}",
        ))

    print("\nExit reason breakdown per mode:")
    for mode, trades in results.items():
        reason_counts: dict[str, int] = defaultdict(int)
        for t in trades:
            reason_counts[t["exit_reason"]] += 1
        print(f"\n  {mode.upper()}:")
        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
            print(f"    {reason:<42} {count:>5}")

    print(f"\nTrade logs → reports/ma_vwap_open_<mode>_trades.csv")
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--months",   type=int,   default=6)
    parser.add_argument("--symbols",  nargs="+",  default=None)
    parser.add_argument("--stop-pct", type=float, default=1.0)
    parser.add_argument("--mode",     default=None,
                        choices=["vwap", "target", "prev_vwap"],
                        help="Run only this mode (default: all three)")
    args = parser.parse_args()

    universe = list(dict.fromkeys(args.symbols or NIFTY_50))
    modes    = [args.mode] if args.mode else ["vwap", "target", "prev_vwap"]

    auth = KiteAuth()
    kite = auth.authenticated_client()

    instruments    = fetch_instruments(kite, "NSE")
    symbol_to_token = {i["tradingsymbol"]: i["instrument_token"] for i in instruments}

    to_date   = datetime.now()
    from_date = to_date - timedelta(days=args.months * 30)

    print(f"MA9>MA21 + VWAP backtest  |  {args.months} months  |  {len(universe)} symbols")
    print(f"Window: {from_date.date()} → {to_date.date()}")
    print(f"Modes:  {', '.join(modes)}   Stop: {args.stop_pct}%   Target: {_TARGET_R}R\n")
    print("Candles are fetched once and shared across all modes.")

    candle_cache: dict[str, list[dict]] = {}
    results: dict[str, list[dict]] = {}

    for mode in modes:
        results[mode] = run_variant(
            mode, universe, symbol_to_token, kite,
            from_date, to_date, args.stop_pct, candle_cache,
        )

    _print_comparison(results)


if __name__ == "__main__":
    main()
