"""Backtest: ORB + Trend Confirmation strategy on Nifty 50, 1-minute candles.

Setup  : 9:15–9:30  mark opening-range high/low
Entry  : 9:30–9:45  breakout above ORB high (long) or below ORB low (short)
         with MA9>MA21, close>VWAP (long) / close<VWAP (short), volume>1.5×avg
Stop   : tighter of ATR(14)×1.5 vs ORB midpoint
Target : 1.5R; trails MA9 once 1R achieved
Exits  : stop, target, MA21 trend break, 3:15 PM
Range  : 0.3%–1.5% of ORB low (skip too tight / too volatile days)

Run:
    python scripts/backtest_orb_trend.py
    python scripts/backtest_orb_trend.py --months 3
    python scripts/backtest_orb_trend.py --symbols RELIANCE TCS INFY
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
from strategy.orb_trend import ORBTrendStrategy  # noqa: E402

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

_SQUARE_OFF   = dt_time(15, 20)
_SLIPPAGE_BPS = 5.0
_CAPITAL      = 100_000.0
_RISK_PCT     = 1.0          # % of capital risked per trade
_OUTPUT_DIR   = REPO_ROOT / "reports"


def position_size(entry: float, stop: float) -> int:
    risk = abs(entry - stop)
    if risk <= 0:
        return 0
    return max(int((_CAPITAL * _RISK_PCT / 100) / risk), 1)


def replay_symbol(symbol: str, candles: list[dict], strategy: ORBTrendStrategy) -> list[dict]:
    trades: list[dict] = []
    open_pos: dict | None = None

    by_day: dict[str, list[dict]] = defaultdict(list)
    for c in candles:
        ts  = c["date"]
        day = ts.date().isoformat() if isinstance(ts, datetime) else str(ts)[:10]
        by_day[day].append(c)

    for day in sorted(by_day):
        last_candle: dict | None = None

        for c in by_day[day]:
            ts = c["date"]
            if not isinstance(ts, datetime) or ts.time() > _SQUARE_OFF:
                break
            last_candle = c

            sig = strategy.on_candle(symbol, c)
            if sig is None:
                continue

            if sig.action.value in ("BUY", "SELL") and open_pos is None:
                side    = sig.action.value          # "BUY" (long) or "SELL" (short)
                fill    = apply_slippage(side, sig.price, _SLIPPAGE_BPS)
                stop    = sig.stop_price or fill * 0.99
                qty     = position_size(fill, stop)
                cost    = calculate_transaction_cost(side, fill, qty, "MIS")
                open_pos = {
                    "price": fill, "cost": cost, "qty": qty,
                    "side": side, "ts": ts, "day": day, "reason": sig.reason,
                    "stop": stop,
                }

            elif sig.action.value == "EXIT" and open_pos is not None:
                exit_side = "SELL" if open_pos["side"] == "BUY" else "BUY"
                fill      = apply_slippage(exit_side, sig.price, _SLIPPAGE_BPS)
                exit_cost = calculate_transaction_cost(exit_side, fill, open_pos["qty"], "MIS")
                if open_pos["side"] == "BUY":
                    gross = (fill - open_pos["price"]) * open_pos["qty"]
                else:
                    gross = (open_pos["price"] - fill) * open_pos["qty"]
                trades.append(_row(symbol, open_pos, fill, ts, gross,
                                   open_pos["cost"] + exit_cost, sig.reason))
                open_pos = None

        # EOD square-off
        if open_pos is not None and last_candle is not None:
            sig = strategy.force_exit(symbol, last_candle["close"], last_candle["date"])
            if sig:
                exit_side = "SELL" if open_pos["side"] == "BUY" else "BUY"
                fill      = apply_slippage(exit_side, last_candle["close"], _SLIPPAGE_BPS)
                exit_cost = calculate_transaction_cost(exit_side, fill, open_pos["qty"], "MIS")
                if open_pos["side"] == "BUY":
                    gross = (fill - open_pos["price"]) * open_pos["qty"]
                else:
                    gross = (open_pos["price"] - fill) * open_pos["qty"]
                trades.append(_row(symbol, open_pos, fill, last_candle["date"],
                                   gross, open_pos["cost"] + exit_cost, "EOD square-off"))
                open_pos = None

    return trades


def _row(symbol, pos, exit_price, exit_ts, gross, total_cost, exit_reason) -> dict:
    return {
        "symbol":       symbol,
        "day":          pos["day"],
        "direction":    pos["side"],
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


def _summary(trades: list[dict]) -> dict:
    if not trades:
        return dict(trades=0, wins=0, win_rate=0.0, gross=0.0,
                    cost=0.0, net=0.0, avg_win=0.0, avg_loss=0.0,
                    long_trades=0, short_trades=0)
    wins   = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    longs  = [t for t in trades if t["direction"] == "BUY"]
    shorts = [t for t in trades if t["direction"] == "SELL"]
    return dict(
        trades=len(trades), wins=len(wins),
        win_rate=len(wins) / len(trades) * 100,
        gross=sum(t["gross_pnl"] for t in trades),
        cost=sum(t["cost"] for t in trades),
        net=sum(t["net_pnl"] for t in trades),
        avg_win=sum(t["net_pnl"] for t in wins) / len(wins) if wins else 0.0,
        avg_loss=sum(t["net_pnl"] for t in losses) / len(losses) if losses else 0.0,
        long_trades=len(longs),
        short_trades=len(shorts),
    )


def _print_report(trades: list[dict]) -> None:
    s = _summary(trades)
    print("\n" + "=" * 72)
    print("ORB + TREND CONFIRMATION  |  5-min  |  09:15–09:30 ORB  |  Nifty50")
    print("=" * 72)
    print(f"  Trades     : {s['trades']}  (long={s['long_trades']}, short={s['short_trades']})")
    print(f"  Win rate   : {s['win_rate']:.1f}%  ({s['wins']} wins)")
    print(f"  Gross P&L  : ₹{s['gross']:>12,.0f}")
    print(f"  Costs      : ₹{s['cost']:>12,.0f}")
    print(f"  Net P&L    : ₹{s['net']:>12,.0f}")
    print(f"  Avg win    : ₹{s['avg_win']:>12,.0f}")
    print(f"  Avg loss   : ₹{s['avg_loss']:>12,.0f}")

    print("\nExit reason breakdown:")
    counts: dict[str, int] = defaultdict(int)
    for t in trades:
        counts[t["exit_reason"]] += 1
    for reason, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"    {reason:<50} {n:>5}")

    print(f"\nCSV log → reports/orb_trend_trades.csv")
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--months",  type=int, default=6)
    parser.add_argument("--symbols", nargs="+", default=None)
    args = parser.parse_args()

    universe = list(dict.fromkeys(args.symbols or NIFTY_50))

    auth            = KiteAuth()
    kite            = auth.authenticated_client()
    instruments     = fetch_instruments(kite, "NSE")
    symbol_to_token = {i["tradingsymbol"]: i["instrument_token"] for i in instruments}

    to_date   = datetime.now()
    from_date = to_date - timedelta(days=args.months * 30)

    print(f"ORB Trend backtest  |  5-min candles  |  {args.months} months  |  {len(universe)} symbols")
    print(f"Window: {from_date.date()} → {to_date.date()}")
    print()

    all_trades: list[dict] = []
    missing: list[str]     = []

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = _OUTPUT_DIR / "orb_trend_trades.csv"
    csv_file = open(csv_path, "w", newline="")
    csv_writer = None

    for idx, symbol in enumerate(universe, 1):
        token = symbol_to_token.get(symbol)
        if token is None:
            missing.append(symbol)
            print(f"  [{idx:>2}/{len(universe)}] {symbol:<14} SKIP (unknown)")
            continue

        candles  = fetch_historical_candles(kite, token, from_date, to_date, "5minute")
        strategy = ORBTrendStrategy()
        trades   = replay_symbol(symbol, candles, strategy)
        del candles

        sym_net  = sum(t["net_pnl"] for t in trades)
        sym_wins = sum(1 for t in trades if t["net_pnl"] > 0)
        longs    = sum(1 for t in trades if t["direction"] == "BUY")
        shorts   = sum(1 for t in trades if t["direction"] == "SELL")

        print(f"  [{idx:>2}/{len(universe)}] {symbol:<14} "
              f"{len(trades):>4}t  L={longs} S={shorts}  "
              f"w={sym_wins}  ₹{sym_net:,.0f}")

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

    _print_report(all_trades)


if __name__ == "__main__":
    main()
