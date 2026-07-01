"""Backtest: MA9 > MA21 + close > VWAP, 1-minute candles, 09:15–09:45 entry
window, all current Nifty 50 stocks, past 6 months.

Memory-efficient: fetches, replays, and discards one symbol at a time so the
process never holds more than ~45k candles in memory (one symbol's 6-month
worth), avoiding OOM on 1 GB servers.

Run:
    python scripts/backtest_ma_vwap_open.py
    python scripts/backtest_ma_vwap_open.py --months 3 --stop-pct 1.5
    python scripts/backtest_ma_vwap_open.py --symbols RELIANCE TCS INFY

Output:
    Prints a per-symbol and aggregate P&L report to stdout.
    Writes a detailed trade log to reports/ma_vwap_open_trades.csv.
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

# Current Nifty 50 constituents (Kite tradingsymbols, NSE).
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
    "BEL", "ZOMATO", "BAJAJFINSV", "LTIM", "TATAMOTORS",
]

_SQUARE_OFF = dt_time(15, 20)
_SLIPPAGE_BPS = 5.0
_CAPITAL = 100_000.0
_RISK_PCT = 1.0
_OUTPUT_DIR = REPO_ROOT / "reports"


def position_size(entry: float, stop: float, capital: float, risk_pct: float) -> int:
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return 0
    qty = int((capital * risk_pct / 100) / risk_per_share)
    return max(qty, 1)


def replay_symbol(
    symbol: str,
    candles: list[dict],
    strategy: MAVWAPOpenStrategy,
    stop_pct: float,
    capital: float,
) -> list[dict]:
    """Replay one symbol's candles through the strategy and return its trades."""
    trades: list[dict] = []
    open_pos: dict | None = None

    # Group by day to handle EOD square-off correctly.
    by_day: dict[str, list[dict]] = defaultdict(list)
    for c in candles:
        ts = c["date"]
        day = ts.date().isoformat() if isinstance(ts, datetime) else str(ts)[:10]
        by_day[day].append(c)

    for day in sorted(by_day):
        day_candles = by_day[day]
        last_candle: dict | None = None

        for candle in day_candles:
            ts = candle["date"]
            if not isinstance(ts, datetime):
                continue
            if ts.time() > _SQUARE_OFF:
                break
            last_candle = candle

            sig = strategy.on_candle(symbol, candle)
            if sig is None:
                continue

            if sig.action.value == "BUY" and open_pos is None:
                fill = apply_slippage("BUY", sig.price, _SLIPPAGE_BPS)
                stop = sig.stop_price or fill * (1 - stop_pct / 100)
                qty = position_size(fill, stop, capital, _RISK_PCT)
                cost = calculate_transaction_cost("BUY", fill, qty, "MIS")
                open_pos = {
                    "entry_price": fill,
                    "entry_cost": cost,
                    "qty": qty,
                    "entry_ts": ts,
                    "entry_reason": sig.reason,
                    "day": day,
                }

            elif sig.action.value == "EXIT" and open_pos is not None:
                fill = apply_slippage("SELL", sig.price, _SLIPPAGE_BPS)
                exit_cost = calculate_transaction_cost("SELL", fill, open_pos["qty"], "MIS")
                gross = (fill - open_pos["entry_price"]) * open_pos["qty"]
                total_cost = open_pos["entry_cost"] + exit_cost
                trades.append(_trade_row(symbol, open_pos, fill, ts, gross, total_cost, sig.reason))
                open_pos = None

        # EOD square-off.
        if open_pos is not None and last_candle is not None:
            exit_sig = strategy.force_exit(symbol, last_candle["close"], last_candle["date"])
            if exit_sig:
                fill = apply_slippage("SELL", last_candle["close"], _SLIPPAGE_BPS)
                exit_cost = calculate_transaction_cost("SELL", fill, open_pos["qty"], "MIS")
                gross = (fill - open_pos["entry_price"]) * open_pos["qty"]
                total_cost = open_pos["entry_cost"] + exit_cost
                trades.append(_trade_row(symbol, open_pos, fill, last_candle["date"], gross, total_cost, "EOD square-off"))
                open_pos = None

    return trades


def _trade_row(symbol, pos, exit_price, exit_ts, gross, total_cost, exit_reason) -> dict:
    return {
        "symbol": symbol,
        "day": pos["day"],
        "entry_ts": pos["entry_ts"].isoformat(),
        "exit_ts": exit_ts.isoformat() if isinstance(exit_ts, datetime) else str(exit_ts),
        "entry_price": round(pos["entry_price"], 2),
        "exit_price": round(exit_price, 2),
        "qty": pos["qty"],
        "gross_pnl": round(gross, 2),
        "cost": round(total_cost, 2),
        "net_pnl": round(gross - total_cost, 2),
        "exit_reason": exit_reason,
        "entry_reason": pos["entry_reason"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--stop-pct", type=float, default=1.0)
    parser.add_argument("--capital", type=float, default=_CAPITAL)
    args = parser.parse_args()

    universe = list(dict.fromkeys(args.symbols or NIFTY_50))  # deduplicate, preserve order

    auth = KiteAuth()
    kite = auth.authenticated_client()

    instruments = fetch_instruments(kite, "NSE")
    symbol_to_token = {i["tradingsymbol"]: i["instrument_token"] for i in instruments}

    to_date = datetime.now()
    from_date = to_date - timedelta(days=args.months * 30)

    print(f"Fetching {args.months} months of 1-minute candles for {len(universe)} symbols")
    print(f"Window: {from_date.date()} → {to_date.date()}")
    print("Processing one symbol at a time to stay within memory limits.\n")

    all_trades: list[dict] = []
    missing: list[str] = []

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = _OUTPUT_DIR / "ma_vwap_open_trades.csv"
    csv_file = open(csv_path, "w", newline="")
    csv_writer: csv.DictWriter | None = None

    for idx, symbol in enumerate(universe, 1):
        token = symbol_to_token.get(symbol)
        if token is None:
            missing.append(symbol)
            print(f"  [{idx:>2}/{len(universe)}] {symbol:<14} SKIP (unknown symbol)")
            continue

        candles = fetch_historical_candles(kite, token, from_date, to_date, "minute")
        n = len(candles)

        # One strategy instance per symbol keeps state isolated.
        strategy = MAVWAPOpenStrategy(ma_fast=9, ma_slow=21, stop_pct=args.stop_pct)
        trades = replay_symbol(symbol, candles, strategy, args.stop_pct, args.capital)

        # Free candle memory immediately.
        del candles

        sym_net = sum(t["net_pnl"] for t in trades)
        sym_wins = sum(1 for t in trades if t["net_pnl"] > 0)
        print(f"  [{idx:>2}/{len(universe)}] {symbol:<14} {n:>7} candles  "
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
        print(f"\n  [SKIP] unrecognised: {', '.join(missing)}")

    _print_report(all_trades, args)


def _print_report(trades: list[dict], args) -> None:
    print()
    if not trades:
        print("No trades executed.")
        return

    total_gross = sum(t["gross_pnl"] for t in trades)
    total_cost = sum(t["cost"] for t in trades)
    total_net = sum(t["net_pnl"] for t in trades)
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]

    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_symbol[t["symbol"]].append(t)

    print("=" * 62)
    print("MA9>MA21 + PRICE>VWAP  |  1-min  |  09:15–09:45 entry")
    print("=" * 62)
    print(f"Capital:        ₹{args.capital:,.0f}   Risk/trade: {_RISK_PCT}%   Stop: {args.stop_pct}%")
    print(f"Total trades:   {len(trades)}")
    print(f"Wins / Losses:  {len(wins)} / {len(losses)}   "
          f"Win rate: {len(wins)/len(trades)*100:.1f}%")
    print(f"Gross P&L:      ₹{total_gross:,.2f}")
    print(f"Total costs:    ₹{total_cost:,.2f}")
    print(f"Net P&L:        ₹{total_net:,.2f}")
    if wins:
        print(f"Avg win (net):  ₹{sum(t['net_pnl'] for t in wins)/len(wins):,.2f}")
    if losses:
        print(f"Avg loss (net): ₹{sum(t['net_pnl'] for t in losses)/len(losses):,.2f}")

    print("\nPer-symbol breakdown (sorted by net P&L):")
    print(f"  {'Symbol':<14} {'Trades':>6} {'Wins':>5} {'Net P&L':>10} {'Avg/trade':>10}")
    print("  " + "-" * 52)
    for sym in sorted(by_symbol, key=lambda s: -sum(t["net_pnl"] for t in by_symbol[s])):
        sym_trades = by_symbol[sym]
        sym_net = sum(t["net_pnl"] for t in sym_trades)
        sym_wins = sum(1 for t in sym_trades if t["net_pnl"] > 0)
        avg = sym_net / len(sym_trades)
        print(f"  {sym:<14} {len(sym_trades):>6} {sym_wins:>5} {sym_net:>10.2f} {avg:>10.2f}")

    print("\nExit reason breakdown:")
    reason_counts: dict[str, int] = defaultdict(int)
    for t in trades:
        reason_counts[t["exit_reason"]] += 1
    for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
        print(f"  {reason:<42} {count:>4}")

    print(f"\nFull trade log → reports/ma_vwap_open_trades.csv")
    print("=" * 62)


if __name__ == "__main__":
    main()
