"""Builds a human-readable summary report from the trade/signal/P&L tables.

Printed to stdout at the end of a run (backtest completion, or live/paper
shutdown) so there's a single place to see what the bot actually did
without having to query the SQLite file by hand.
"""
from __future__ import annotations

from storage.db import cursor


def _fetch_all(query: str, params: tuple = ()) -> list[tuple]:
    with cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def build_report(mode: str, open_positions: dict | None = None) -> str:
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append(f"TRADING REPORT (mode={mode})")
    lines.append("=" * 60)

    trades = _fetch_all(
        "SELECT ts, symbol, side, quantity, price, pnl, status FROM trades WHERE mode = ? ORDER BY id",
        (mode,),
    )
    closed_trades = [t for t in trades if t[5] is not None]
    total_pnl = sum(t[5] for t in closed_trades)
    wins = [t for t in closed_trades if t[5] > 0]
    losses = [t for t in closed_trades if t[5] < 0]
    win_rate = (len(wins) / len(closed_trades) * 100) if closed_trades else 0.0

    lines.append(f"Total trade legs (entries + exits): {len(trades)}")
    lines.append(f"Closed round trips: {len(closed_trades)}")
    lines.append(f"Wins: {len(wins)}  Losses: {len(losses)}  Win rate: {win_rate:.1f}%")
    lines.append(f"Total realized P&L: {total_pnl:.2f}")
    if closed_trades:
        avg_win = sum(t[5] for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(t[5] for t in losses) / len(losses) if losses else 0.0
        lines.append(f"Avg win: {avg_win:.2f}  Avg loss: {avg_loss:.2f}")

    by_symbol = _fetch_all(
        """SELECT symbol, COUNT(*), COALESCE(SUM(pnl), 0)
           FROM trades WHERE mode = ? AND pnl IS NOT NULL GROUP BY symbol ORDER BY symbol""",
        (mode,),
    )
    if by_symbol:
        lines.append("")
        lines.append("Per-symbol (closed trades, total P&L):")
        for symbol, count, pnl in by_symbol:
            lines.append(f"  {symbol:<12} trades={count:<4} pnl={pnl:.2f}")

    daily = _fetch_all("SELECT trade_date, realized_pnl, trade_count, halted FROM daily_pnl ORDER BY trade_date")
    lines.append("")
    lines.append(f"Trading days recorded: {len(daily)}")
    halted_days = [d for d in daily if d[3]]
    if halted_days:
        lines.append(f"Days halted by loss cap: {len(halted_days)} -> {[d[0] for d in halted_days]}")
    if daily:
        best = max(daily, key=lambda d: d[1])
        worst = min(daily, key=lambda d: d[1])
        lines.append(f"Best day: {best[0]} ({best[1]:.2f})  Worst day: {worst[0]} ({worst[1]:.2f})")

    open_positions = open_positions or {}
    lines.append("")
    if open_positions:
        lines.append(f"WARNING: {len(open_positions)} position(s) still open at report time: {list(open_positions.keys())}")
    else:
        lines.append("All positions closed: yes")

    errors = _fetch_all("SELECT COUNT(*) FROM errors")
    lines.append(f"Errors logged: {errors[0][0]}")
    lines.append("=" * 60)

    if trades:
        lines.append("")
        lines.append("All trades:")
        lines.append(f"  {'ts':<28}{'symbol':<12}{'side':<6}{'qty':>6}  {'price':>10}  {'pnl':>10}  status")
        for ts, symbol, side, quantity, price, pnl, status in trades:
            pnl_str = f"{pnl:.2f}" if pnl is not None else "-"
            lines.append(f"  {ts:<28}{symbol:<12}{side:<6}{quantity:>6}  {price:>10.2f}  {pnl_str:>10}  {status}")
        lines.append("=" * 60)

    return "\n".join(lines)


def print_report(mode: str, open_positions: dict | None = None) -> None:
    print(build_report(mode, open_positions))
