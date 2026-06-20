"""Main orchestrator: wires config, data, strategy, risk and execution
together for whichever mode is configured, and registers the EOD square-off.
"""
from __future__ import annotations

import time
from datetime import datetime, time as dt_time, timedelta

from config.settings import settings
from execution.backtest import BacktestExecutor
from execution.base import OrderExecutor
from execution.live import LiveExecutor
from execution.paper import PaperExecutor
from scheduler.scheduler import start_square_off_scheduler
from storage.db import log_error, log_signal, reset_backtest_data, update_last_trade_pnl
from storage.report import print_report, write_report
from strategy.base import Action, Signal
from strategy.momentum_halfhour import MomentumHalfHourStrategy
from strategy.orb import ORBStrategy
from strategy.pairs import PairsStrategy, load_pairs_config


class TraderApp:
    def __init__(self, strategy, executor: OrderExecutor, risk_manager, price_fetcher=None):
        self.strategy = strategy
        self.executor = executor
        self.risk_manager = risk_manager
        # Looks up the current market price for a symbol, used at EOD
        # square-off time. Falls back to entry_price (zero recorded P&L)
        # only if no fetcher is wired up, e.g. in tests.
        self.price_fetcher = price_fetcher
        # symbol -> (action, quantity, entry_price, entry_cost)
        self.open_positions: dict[str, tuple[str, int, float, float]] = {}
        # pair_id -> (symbol_a, side_a, qty_a, entry_price_a, entry_cost_a,
        #              symbol_b, side_b, qty_b, entry_price_b, entry_cost_b)
        self.open_pair_positions: dict[str, tuple] = {}

    def handle_candle(self, symbol: str, candle: dict) -> None:
        candle_date = candle["date"]
        trade_date = candle_date.date().isoformat() if isinstance(candle_date, datetime) else str(candle_date)[:10]
        signal = self.strategy.on_candle(symbol, candle)
        if signal is None:
            return
        if signal.pair_id is not None:
            self._handle_pair_signal(signal, trade_date)
        else:
            self._handle_signal(signal, trade_date)

    def _handle_pair_signal(self, signal: Signal, trade_date: str) -> None:
        log_signal(signal.strategy, signal.symbol, signal.action.value, signal.price, signal.reason, ts=signal.ts)

        if not self.risk_manager.approve_signal(trade_date):
            return

        if signal.action == Action.EXIT:
            position = self.open_pair_positions.pop(signal.pair_id, None)
            if position is None:
                return
            symbol_a, side_a, qty_a, entry_a, entry_cost_a, symbol_b, side_b, qty_b, entry_b, entry_cost_b = position
            exit_side_a = Action.SELL if side_a == "BUY" else Action.BUY
            exit_side_b = Action.SELL if side_b == "BUY" else Action.BUY
            try:
                fill_a = self.executor.execute(Signal(signal.strategy, symbol_a, exit_side_a, signal.price, pair_id=signal.pair_id, ts=signal.ts), qty_a)
                fill_b = self.executor.execute(Signal(signal.strategy, symbol_b, exit_side_b, signal.leg2_price, pair_id=signal.pair_id, ts=signal.ts), qty_b)
            except Exception as exc:
                self.risk_manager.record_error("executor", str(exc))
                return
            self.risk_manager.record_success()
            pnl_a = (fill_a.price - entry_a) * qty_a if side_a == "BUY" else (entry_a - fill_a.price) * qty_a
            pnl_b = (fill_b.price - entry_b) * qty_b if side_b == "BUY" else (entry_b - fill_b.price) * qty_b
            net_pnl_a = pnl_a - entry_cost_a - fill_a.cost
            net_pnl_b = pnl_b - entry_cost_b - fill_b.cost
            net_pnl = net_pnl_a + net_pnl_b
            self.risk_manager.record_pnl(trade_date, net_pnl)
            update_last_trade_pnl(symbol_a, pnl_a, net_pnl_a)
            update_last_trade_pnl(symbol_b, pnl_b, net_pnl_b)
            return

        qty_a, qty_b = self.risk_manager.pairs_position_size(signal.price, signal.leg2_price, signal.hedge_ratio)
        if qty_a <= 0 or qty_b <= 0:
            if hasattr(self.strategy, "cancel_pair"):
                self.strategy.cancel_pair(signal.pair_id)
            return
        try:
            fill_a = self.executor.execute(signal, qty_a)
            leg2_signal = Signal(signal.strategy, signal.leg2_symbol, signal.leg2_action, signal.leg2_price, pair_id=signal.pair_id, ts=signal.ts)
            fill_b = self.executor.execute(leg2_signal, qty_b)
        except Exception as exc:
            self.risk_manager.record_error("executor", str(exc))
            return
        self.risk_manager.record_success()
        if fill_a.status in ("FILLED", "PLACED") and fill_b.status in ("FILLED", "PLACED"):
            self.open_pair_positions[signal.pair_id] = (
                signal.symbol, fill_a.side, fill_a.quantity, fill_a.price, fill_a.cost,
                signal.leg2_symbol, fill_b.side, fill_b.quantity, fill_b.price, fill_b.cost,
            )

    def _handle_signal(self, signal: Signal, trade_date: str) -> None:
        log_signal(signal.strategy, signal.symbol, signal.action.value, signal.price, signal.reason, ts=signal.ts)

        if not self.risk_manager.approve_signal(trade_date):
            return

        if signal.action == Action.EXIT:
            self._exit_position(signal, trade_date)
            return

        if signal.stop_price is not None:
            stop_loss_price = signal.stop_price
        else:
            stop_loss_price = signal.price * (0.99 if signal.action == Action.BUY else 1.01)
        quantity = self.risk_manager.position_size(signal.price, stop_loss_price)
        if quantity <= 0:
            return

        try:
            fill = self.executor.execute(signal, quantity)
        except Exception as exc:
            self.risk_manager.record_error("executor", str(exc))
            return

        self.risk_manager.record_success()
        if fill.status in ("FILLED", "PLACED"):
            self.open_positions[signal.symbol] = (fill.side, fill.quantity, fill.price, fill.cost)

    def _exit_position(self, signal: Signal, trade_date: str) -> None:
        position = self.open_positions.pop(signal.symbol, None)
        if position is None:
            return
        side, quantity, entry_price, entry_cost = position
        exit_side = Action.SELL if side == "BUY" else Action.BUY
        exit_signal = Signal(signal.strategy, signal.symbol, exit_side, signal.price, reason=signal.reason, ts=signal.ts)

        try:
            fill = self.executor.execute(exit_signal, quantity)
        except Exception as exc:
            self.risk_manager.record_error("executor", str(exc))
            return

        self.risk_manager.record_success()
        pnl = (fill.price - entry_price) * quantity if side == "BUY" else (entry_price - fill.price) * quantity
        net_pnl = pnl - entry_cost - fill.cost
        self.risk_manager.record_pnl(trade_date, net_pnl)
        update_last_trade_pnl(signal.symbol, pnl, net_pnl)

    def square_off_symbol(self, symbol: str, price: float, trade_date: str, ts=None) -> None:
        if symbol not in self.open_positions:
            return
        self._exit_position(Signal(self.strategy.name, symbol, Action.EXIT, price, reason="EOD square-off", ts=ts), trade_date)

    def square_off_pair(self, pair_id: str, price_a: float, price_b: float, trade_date: str, ts=None) -> None:
        if pair_id not in self.open_pair_positions:
            return
        symbol_a, _, _, _, _, symbol_b, _, _, _, _ = self.open_pair_positions[pair_id]
        self._handle_pair_signal(
            Signal(
                self.strategy.name, symbol_a, Action.EXIT, price_a, reason="EOD square-off", ts=ts,
                pair_id=pair_id, leg2_symbol=symbol_b, leg2_action=Action.EXIT, leg2_price=price_b,
            ),
            trade_date,
        )

    def square_off_all(self) -> None:
        now = datetime.now()
        today = now.date().isoformat()
        for symbol, (side, quantity, entry_price, entry_cost) in list(self.open_positions.items()):
            price = entry_price
            if self.price_fetcher is not None:
                fetched = self.price_fetcher(symbol)
                if fetched:
                    price = fetched
                else:
                    self.risk_manager.record_error(
                        "square_off", f"could not fetch live price for {symbol}; squaring off at entry price"
                    )
            self.square_off_symbol(symbol, price, today, ts=now)

        for pair_id, (symbol_a, _, _, entry_a, _, symbol_b, _, _, entry_b, _) in list(self.open_pair_positions.items()):
            price_a, price_b = entry_a, entry_b
            if self.price_fetcher is not None:
                fetched_a = self.price_fetcher(symbol_a)
                fetched_b = self.price_fetcher(symbol_b)
                if fetched_a and fetched_b:
                    price_a, price_b = fetched_a, fetched_b
                else:
                    self.risk_manager.record_error(
                        "square_off", f"could not fetch live price for pair {pair_id}; squaring off at entry price"
                    )
            self.square_off_pair(pair_id, price_a, price_b, today, ts=now)


def build_strategy():
    if settings.strategy == "momentum_halfhour":
        return MomentumHalfHourStrategy(
            settings.momentum_window_minutes,
            settings.momentum_volume_multiplier,
            settings.momentum_volume_lookback,
        )
    if settings.strategy == "pairs":
        pairs = load_pairs_config(settings.pairs_config_path)
        return PairsStrategy(pairs, settings.pairs_spread_lookback, settings.pairs_entry_z, settings.pairs_exit_z)
    return ORBStrategy(
        settings.orb_range_minutes,
        settings.orb_candle_interval,
        settings.orb_volume_multiplier,
        settings.orb_volume_lookback,
        settings.orb_nr7_lookback,
        settings.orb_entry_cutoff_time,
        settings.orb_target_r,
    )


def build_executor(mode: str) -> OrderExecutor:
    if mode == "backtest":
        return BacktestExecutor()
    if mode == "paper":
        return PaperExecutor()
    if mode == "live":
        from auth.kite_auth import KiteAuth

        auth = KiteAuth()
        kite = auth.authenticated_client()
        return LiveExecutor(kite)
    raise ValueError(f"Unknown mode: {mode}")


def run_backtest() -> None:
    from auth.kite_auth import KiteAuth
    from data.historical import fetch_historical_candles
    from risk.risk_manager import RiskManager
    from strategy.scanner import (
        compute_narrow_range_days,
        compute_volume_ok_days,
        group_candles_by_day,
        rank_candidates,
        scan_backtest_day,
    )

    reset_backtest_data()

    auth = KiteAuth()
    kite = auth.authenticated_client()

    strategy = build_strategy()
    risk_manager = RiskManager(
        settings.capital,
        settings.risk_pct_per_trade,
        settings.daily_loss_cap_pct,
        settings.max_consecutive_errors,
        settings.pairs_capital_pct,
    )
    app = TraderApp(strategy, BacktestExecutor(), risk_manager)

    instruments = kite.instruments("NSE")
    symbol_to_token = {i["tradingsymbol"]: i["instrument_token"] for i in instruments}

    universe = settings.scan_universe or settings.watchlist
    range_candles = max(1, settings.orb_range_minutes // int(settings.orb_candle_interval.replace("minute", "") or 1))
    square_off_hour, square_off_minute = (int(part) for part in settings.square_off_time.split(":"))
    square_off_cutoff = dt_time(square_off_hour, square_off_minute)

    # Fetch extra history before the backtest window so the relative-volume
    # baseline has lookback data even on the first simulated day.
    backtest_start = datetime.now() - timedelta(days=settings.backtest_days)
    from_date = backtest_start - timedelta(days=settings.scan_lookback_days * 2 + 5)
    to_date = datetime.now()

    candles_by_symbol_day: dict[str, dict[str, list[dict]]] = {}
    for symbol in universe:
        token = symbol_to_token.get(symbol)
        if token is None:
            log_error("backtest", f"unknown symbol {symbol}")
            continue
        candles = fetch_historical_candles(kite, token, from_date, to_date, settings.orb_candle_interval)
        candles_by_symbol_day[symbol] = group_candles_by_day(candles)

    all_days = sorted({day for by_day in candles_by_symbol_day.values() for day in by_day})
    backtest_start_day = backtest_start.date().isoformat()
    trading_days = [day for day in all_days if day >= backtest_start_day]

    # Computed from each symbol's full fetched history (not just the days it
    # gets selected into the watchlist), so the NR7 filter doesn't spend the
    # first few months "warming up" before it can ever qualify a day.
    narrow_range_days = {
        symbol: compute_narrow_range_days(by_day, settings.orb_nr7_lookback)
        for symbol, by_day in candles_by_symbol_day.items()
    }
    # Only the momentum strategy reads an externally-stamped "_volume_ok";
    # ORB tracks its own breakout-candle volume baseline internally.
    volume_ok_days = (
        {
            symbol: compute_volume_ok_days(
                by_day, settings.momentum_window_minutes, settings.momentum_volume_multiplier, settings.momentum_volume_lookback
            )
            for symbol, by_day in candles_by_symbol_day.items()
        }
        if settings.strategy == "momentum_halfhour"
        else {}
    )

    last_close_per_symbol: dict[str, float] = {}

    for day in trading_days:
        candidates = scan_backtest_day(
            candles_by_symbol_day,
            all_days,
            day,
            range_candles,
            settings.scan_lookback_days,
            last_close_per_symbol,
        )
        todays_watchlist = rank_candidates(
            candidates, settings.scan_top_n, settings.scan_min_relative_volume, settings.scan_min_gap_pct
        )

        for symbol in todays_watchlist:
            day_candles = candles_by_symbol_day[symbol][day]
            # Mirror the live bot's scheduled square-off: don't simulate
            # trading past SQUARE_OFF_TIME, so backtest P&L doesn't include
            # price movement the live bot would never actually capture.
            tradable_candles = [
                c for c in day_candles if not isinstance(c["date"], datetime) or c["date"].time() <= square_off_cutoff
            ]
            if not tradable_candles:
                continue
            is_narrow_range_day = narrow_range_days[symbol].get(day, False)
            is_volume_ok = volume_ok_days.get(symbol, {}).get(day, True)
            for candle in tradable_candles:
                app.handle_candle(
                    symbol, {**candle, "_narrow_range_day": is_narrow_range_day, "_volume_ok": is_volume_ok}
                )
            app.square_off_symbol(symbol, tradable_candles[-1]["close"], day, ts=tradable_candles[-1]["date"])

        for symbol, by_day in candles_by_symbol_day.items():
            day_candles = by_day.get(day)
            if day_candles:
                last_close_per_symbol[symbol] = day_candles[-1]["close"]

    write_report("backtest", settings, app.open_positions)


def run_backtest_pairs() -> None:
    """Pairs trading has a fixed pair list (from screening/pair_finder.py's
    output), not a daily scanner-driven watchlist, and both legs' candles
    must be fed in lockstep by timestamp so PairsStrategy can match them up.
    """
    from auth.kite_auth import KiteAuth
    from data.historical import fetch_historical_candles
    from risk.risk_manager import RiskManager

    reset_backtest_data()

    auth = KiteAuth()
    kite = auth.authenticated_client()

    pairs = load_pairs_config(settings.pairs_config_path)
    strategy = PairsStrategy(pairs, settings.pairs_spread_lookback, settings.pairs_entry_z, settings.pairs_exit_z)
    risk_manager = RiskManager(
        settings.capital,
        settings.risk_pct_per_trade,
        settings.daily_loss_cap_pct,
        settings.max_consecutive_errors,
        settings.pairs_capital_pct,
    )
    app = TraderApp(strategy, BacktestExecutor(), risk_manager)

    instruments = kite.instruments("NSE")
    symbol_to_token = {i["tradingsymbol"]: i["instrument_token"] for i in instruments}

    square_off_hour, square_off_minute = (int(part) for part in settings.square_off_time.split(":"))
    square_off_cutoff = dt_time(square_off_hour, square_off_minute)

    backtest_start = datetime.now() - timedelta(days=settings.backtest_days)
    to_date = datetime.now()

    symbols = {symbol for pair in pairs for symbol in (pair.symbol_a, pair.symbol_b)}
    candles_by_symbol: dict[str, list[dict]] = {}
    for symbol in symbols:
        token = symbol_to_token.get(symbol)
        if token is None:
            log_error("backtest", f"unknown symbol {symbol}")
            continue
        candles = fetch_historical_candles(kite, token, backtest_start, to_date, settings.orb_candle_interval)
        candles_by_symbol[symbol] = [
            c for c in candles if not isinstance(c["date"], datetime) or c["date"].time() <= square_off_cutoff
        ]

    # Merge every symbol's candles into one timeline, sorted by timestamp,
    # so both legs of a pair are always handled in true chronological order.
    timeline: list[tuple[object, str, dict]] = []
    for symbol, candles in candles_by_symbol.items():
        for candle in candles:
            timeline.append((candle["date"], symbol, candle))
    timeline.sort(key=lambda item: item[0])

    last_close: dict[str, float] = {}
    current_day = None
    for ts, symbol, candle in timeline:
        day = ts.date().isoformat() if isinstance(ts, datetime) else str(ts)[:10]
        if current_day is not None and day != current_day:
            for pair_id in list(app.open_pair_positions):
                symbol_a, _, _, _, _, symbol_b, _, _, _, _ = app.open_pair_positions[pair_id]
                price_a = last_close.get(symbol_a)
                price_b = last_close.get(symbol_b)
                if price_a is not None and price_b is not None:
                    app.square_off_pair(pair_id, price_a, price_b, current_day, ts=ts)
        current_day = day
        app.handle_candle(symbol, candle)
        last_close[symbol] = candle["close"]

    diagnostics = build_pairs_z_diagnostics(strategy)
    write_report("backtest", settings, app.open_positions, extra=diagnostics)


def build_pairs_z_diagnostics(strategy: PairsStrategy) -> str:
    """Builds the distribution of every z-score the strategy computed, so
    entry_z/exit_z/spread_lookback can be tuned from real numbers instead of
    guesswork when a backtest produces few or no trades."""
    lines = ["-" * 60, "PAIRS Z-SCORE DIAGNOSTICS", "-" * 60]
    for pair_id, history in strategy.z_history.items():
        if not history:
            lines.append(f"{pair_id}: no z-scores computed (insufficient candle data)")
            continue
        sorted_history = sorted(history)
        n = len(sorted_history)
        p95 = sorted_history[int(n * 0.95)]
        p05 = sorted_history[int(n * 0.05)]
        lines.append(
            f"{pair_id}: n={n} min={sorted_history[0]:.2f} p05={p05:.2f} "
            f"mean={sum(history) / n:.2f} p95={p95:.2f} max={sorted_history[-1]:.2f} "
            f"max_abs={max(abs(sorted_history[0]), abs(sorted_history[-1])):.2f}"
        )
    lines.append("-" * 60)
    return "\n".join(lines)


def run_live_or_paper() -> None:
    from auth.kite_auth import KiteAuth
    from data.candle_builder import CandleBuilder
    from data.ticker import LiveTicker
    from risk.risk_manager import RiskManager
    from strategy.scanner import rank_candidates, scan_live_universe

    strategy = build_strategy()
    risk_manager = RiskManager(
        settings.capital,
        settings.risk_pct_per_trade,
        settings.daily_loss_cap_pct,
        settings.max_consecutive_errors,
        settings.pairs_capital_pct,
    )
    executor = build_executor(settings.mode)

    auth = KiteAuth()
    kite = auth.authenticated_client()

    def fetch_ltp(symbol: str) -> float | None:
        try:
            quote = kite.ltp([f"NSE:{symbol}"])
            return quote[f"NSE:{symbol}"]["last_price"]
        except Exception as exc:
            log_error("square_off", f"ltp fetch failed for {symbol}: {exc}")
            return None

    app = TraderApp(strategy, executor, risk_manager, price_fetcher=fetch_ltp)

    start_square_off_scheduler(app.square_off_all)

    if settings.strategy == "pairs":
        # Pairs are fixed by the screener's output, not the daily scanner.
        todays_watchlist = list({s for pair in load_pairs_config(settings.pairs_config_path) for s in (pair.symbol_a, pair.symbol_b)})
    else:
        universe = settings.scan_universe or settings.watchlist
        candidates = scan_live_universe(kite, universe, settings.scan_lookback_days, settings.orb_candle_interval)
        todays_watchlist = rank_candidates(
            candidates, settings.scan_top_n, settings.scan_min_relative_volume, settings.scan_min_gap_pct
        )
        if not todays_watchlist:
            log_error("scanner", "no symbols qualified for today's watchlist; falling back to static watchlist")
            todays_watchlist = settings.watchlist

    instruments = kite.instruments("NSE")
    symbol_to_token = {i["tradingsymbol"]: i["instrument_token"] for i in instruments if i["tradingsymbol"] in todays_watchlist}
    token_to_symbol = {token: symbol for symbol, token in symbol_to_token.items()}

    interval_minutes = int(settings.orb_candle_interval.replace("minute", "") or 1)
    builder = CandleBuilder(interval_minutes * 60, app.handle_candle)

    ticker = LiveTicker(
        settings.kite_api_key,
        settings.kite_access_token,
        list(symbol_to_token.values()),
        token_to_symbol,
        builder,
        on_error=lambda msg: risk_manager.record_error("ticker", msg),
    )
    ticker.start(threaded=True)

    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        ticker.stop()
    finally:
        print_report(settings.mode, app.open_positions)


def main() -> None:
    if settings.mode == "backtest":
        if settings.strategy == "pairs":
            run_backtest_pairs()
        else:
            run_backtest()
    else:
        run_live_or_paper()


if __name__ == "__main__":
    main()
