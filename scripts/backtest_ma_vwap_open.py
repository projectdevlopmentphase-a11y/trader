"""Backtest: MA9 > MA21 + close > VWAP, 1-minute candles, 09:15–09:45 entry
window, all current Nifty 50 stocks, past 6 months.

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
    "M&M", "TECHM", "POWERGRID", "NTPC", "ONGC",
    "TATAMOTORS", "TATASTEEL", "JSWSTEEL", "BPCL", "COALINDIA",
    "GRASIM", "NESTLEIND", "CIPLA", "DRREDDY", "DIVISLAB",
    "ADANIENT", "ADANIPORTS", "INDUSINDBK", "EICHERMOT", "APOLLOHOSP",
    "BRITANNIA", "HEROMOTOCO", "HINDALCO", "SHRIRAMFIN", "TRENT",
    "BEL", "ZOMATO", "BAJAJFINSV", "LTIM", "MM",
]

_SQUARE_OFF = dt_time(15, 20)
_SLIPPAGE_BPS = 5.0
_CAPITAL = 100_000.0
_RISK_PCT = 1.0   # % of capital risked per trade
_OUTPUT_DIR = REPO_ROOT / "reports"


def group_by_day(candles: list[dict]) -> dict[str, list[dict]]:
    by_day: dict[str, list[dict]] = defaultdict(list)
    for c in candles:
        ts = c["date"]
        day = ts.date().isoformat() if isinstance(ts, datetime) else str(ts)[:10]
        by_day[day].append(c)
    return dict(by_day)


def position_size(entry: float, stop: float, capital: float, risk_pct: float) -> int:
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return 0
    rupees_at_risk = capital * risk_pct / 100
    qty = int(rupees_at_risk / risk_per_share)
    return max(qty, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="Override Nifty 50 with a custom list")
    parser.add_argument("--stop-pct", type=float, default=1.0,
                        help="Hard stop below entry price (%)")
    parser.add_argument("--capital", type=float, default=_CAPITAL)
    args = parser.parse_args()

    universe = args.symbols or NIFTY_50
    strategy = MAVWAPOpenStrategy(ma_fast=9, ma_slow=21, stop_pct=args.stop_pct)

    auth = KiteAuth()
    kite = auth.authenticated_client()

    instruments = fetch_instruments(kite, "NSE")
    symbol_to_token = {i["tradingsymbol"]: i["instrument_token"] for i in instruments}

    to_date = datetime.now()
    from_date = to_date - timedelta(days=args.months * 30)

    print(f"Fetching {args.months} months of 1-minute candles for {len(universe)} symbols …")
    print(f"Window: {from_date.date()} → {to_date.date()}\n")

    candles_by_symbol_day: dict[str, dict[str, list[dict]]] = {}
    missing: list[str] = []
    for symbol in universe:
        token = symbol_to_token.get(symbol)
        if token is None:
            missing.append(symbol)
            continue
        candles = fetch_historical_candles(kite, token, from_date, to_date, "minute")
        candles_by_symbol_day[symbol] = group_by_day(candles)
        print(f"  {symbol:<14} {sum(len(v) for v in candles_by_symbol_day[symbol].values()):>7} candles")

    if missing:
        print(f"\n  [SKIP] unrecognised symbols: {', '.join(missing)}")

    # --- Backtest replay ---
    all_days = sorted({day for by_day in candles_by_symbol_day.values() for day in by_day})

    trades: list[dict] = []
    # track open positions: symbol -> {entry_price, qty, entry_ts, entry_candle}
    open_positions: dict[str, dict] = {}

    for day in all_days:
        # Build time-sorted merged timeline for the day across all symbols.
        day_timeline: list[tuple[datetime, str, dict]] = []
        for symbol, by_day in candles_by_symbol_day.items():
            for c in by_day.get(day, []):
                if isinstance(c["date"], datetime):
                    day_timeline.append((c["date"], symbol, c))
        day_timeline.sort(key=lambda x: x[0])

        last_candle: dict[str, dict] = {}

        for ts, symbol, candle in day_timeline:
            t = ts.time()
            if t > _SQUARE_OFF:
                break
            last_candle[symbol] = candle

            sig = strategy.on_candle(symbol, candle)
            if sig is None:
                continue

            if sig.action.value == "BUY" and symbol not in open_positions:
                fill = apply_slippage("BUY", sig.price, _SLIPPAGE_BPS)
                stop = sig.stop_price or fill * (1 - args.stop_pct / 100)
                qty = position_size(fill, stop, args.capital, _RISK_PCT)
                cost = calculate_transaction_cost("BUY", fill, qty, "MIS")
                open_positions[symbol] = {
                    "entry_price": fill,
                    "entry_cost": cost,
                    "qty": qty,
                    "entry_ts": ts,
                    "entry_reason": sig.reason,
                }

            elif sig.action.value == "EXIT" and symbol in open_positions:
                pos = open_positions.pop(symbol)
                fill = apply_slippage("SELL", sig.price, _SLIPPAGE_BPS)
                exit_cost = calculate_transaction_cost("SELL", fill, pos["qty"], "MIS")
                gross = (fill - pos["entry_price"]) * pos["qty"]
                total_cost = pos["entry_cost"] + exit_cost
                trades.append({
                    "symbol": symbol,
                    "day": day,
                    "entry_ts": pos["entry_ts"].isoformat(),
                    "exit_ts": ts.isoformat(),
                    "entry_price": round(pos["entry_price"], 2),
                    "exit_price": round(fill, 2),
                    "qty": pos["qty"],
                    "gross_pnl": round(gross, 2),
                    "cost": round(total_cost, 2),
                    "net_pnl": round(gross - total_cost, 2),
                    "exit_reason": sig.reason,
                    "entry_reason": pos["entry_reason"],
                })

        # EOD square-off for any still-open positions.
        for symbol, pos in list(open_positions.items()):
            last = last_candle.get(symbol)
            if last is None:
                continue
            exit_sig = strategy.force_exit(symbol, last["close"], last["date"])
            if exit_sig:
                open_positions.pop(symbol)
                fill = apply_slippage("SELL", last["close"], _SLIPPAGE_BPS)
                exit_cost = calculate_transaction_cost("SELL", fill, pos["qty"], "MIS")
                gross = (fill - pos["entry_price"]) * pos["qty"]
                total_cost = pos["entry_cost"] + exit_cost
                trades.append({
                    "symbol": symbol,
                    "day": day,
                    "entry_ts": pos["entry_ts"].isoformat(),
                    "exit_ts": last["date"].isoformat(),
                    "entry_price": round(pos["entry_price"], 2),
                    "exit_price": round(fill, 2),
                    "qty": pos["qty"],
                    "gross_pnl": round(gross, 2),
                    "cost": round(total_cost, 2),
                    "net_pnl": round(gross - total_cost, 2),
                    "exit_reason": "EOD square-off",
                    "entry_reason": pos["entry_reason"],
                })

    # --- Report ---
    _print_report(trades, args)
    _write_csv(trades)


def _print_report(trades: list[dict], args) -> None:
    if not trades:
        print("\nNo trades executed.")
        return

    total_gross = sum(t["gross_pnl"] for t in trades)
    total_cost = sum(t["cost"] for t in trades)
    total_net = sum(t["net_pnl"] for t in trades)
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]

    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_symbol[t["symbol"]].append(t)

    print("\n" + "=" * 62)
    print("MA9>MA21 + PRICE>VWAP  |  1-min  |  09:15–09:45 entry")
    print("=" * 62)
    print(f"Capital:       ₹{args.capital:,.0f}   Risk/trade: {_RISK_PCT}%   Stop: {args.stop_pct}%")
    print(f"Total trades:  {len(trades)}")
    print(f"Wins / Losses: {len(wins)} / {len(losses)}   Win rate: {len(wins)/len(trades)*100:.1f}%")
    print(f"Gross P&L:     ₹{total_gross:,.2f}")
    print(f"Total costs:   ₹{total_cost:,.2f}")
    print(f"Net P&L:       ₹{total_net:,.2f}")
    if wins:
        print(f"Avg win (net): ₹{sum(t['net_pnl'] for t in wins)/len(wins):,.2f}")
    if losses:
        print(f"Avg loss (net):₹{sum(t['net_pnl'] for t in losses)/len(losses):,.2f}")

    print("\nPer-symbol breakdown:")
    print(f"  {'Symbol':<14} {'Trades':>6} {'Wins':>5} {'Net P&L':>10} {'Avg net':>9}")
    print("  " + "-" * 50)
    for sym in sorted(by_symbol, key=lambda s: -sum(t["net_pnl"] for t in by_symbol[s])):
        sym_trades = by_symbol[sym]
        sym_net = sum(t["net_pnl"] for t in sym_trades)
        sym_wins = sum(1 for t in sym_trades if t["net_pnl"] > 0)
        avg = sym_net / len(sym_trades)
        print(f"  {sym:<14} {len(sym_trades):>6} {sym_wins:>5} {sym_net:>10.2f} {avg:>9.2f}")

    print("\nExit reason breakdown:")
    reason_counts: dict[str, int] = defaultdict(int)
    for t in trades:
        reason_counts[t["exit_reason"]] += 1
    for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
        print(f"  {reason:<40} {count:>4}")

    print(f"\nTrade log → reports/ma_vwap_open_trades.csv")
    print("=" * 62)


def _write_csv(trades: list[dict]) -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = _OUTPUT_DIR / "ma_vwap_open_trades.csv"
    if not trades:
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=trades[0].keys())
        writer.writeheader()
        writer.writerows(trades)


if __name__ == "__main__":
    main()
