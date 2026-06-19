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
from storage.report import print_report
from strategy.base import Action, Signal
from strategy.orb import ORBStrategy


class TraderApp:
    def __init__(self, strategy, executor: OrderExecutor, risk_manager, price_fetcher=None):
        self.strategy = strategy
        self.executor = executor
        self.risk_manager = risk_manager
        # Looks up the current market price for a symbol, used at EOD
        # square-off time. Falls back to entry_price (zero recorded P&L)
        # only if no fetcher is wired up, e.g. in tests.
        self.price_fetcher = price_fetcher
        # symbol -> (action, quantity, entry_price)
        self.open_positions: dict[str, tuple[str, int, float]] = {}

    def handle_candle(self, symbol: str, candle: dict) -> None:
        candle_date = candle["date"]
        trade_date = candle_date.date().isoformat() if isinstance(candle_date, datetime) else str(candle_date)[:10]
        signal = self.strategy.on_candle(symbol, candle)
        if signal is None:
            return
        self._handle_signal(signal, trade_date)

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
            self.open_positions[signal.symbol] = (fill.side, fill.quantity, fill.price)

    def _exit_position(self, signal: Signal, trade_date: str) -> None:
        position = self.open_positions.pop(signal.symbol, None)
        if position is None:
            return
        side, quantity, entry_price = position
        exit_side = Action.SELL if side == "BUY" else Action.BUY
        exit_signal = Signal(signal.strategy, signal.symbol, exit_side, signal.price, reason=signal.reason, ts=signal.ts)

        try:
            fill = self.executor.execute(exit_signal, quantity)
        except Exception as exc:
            self.risk_manager.record_error("executor", str(exc))
            return

        self.risk_manager.record_success()
        pnl = (fill.price - entry_price) * quantity if side == "BUY" else (entry_price - fill.price) * quantity
        self.risk_manager.record_pnl(trade_date, pnl)
        update_last_trade_pnl(signal.symbol, pnl)

    def square_off_symbol(self, symbol: str, price: float, trade_date: str, ts=None) -> None:
        if symbol not in self.open_positions:
            return
        self._exit_position(Signal(self.strategy.name, symbol, Action.EXIT, price, reason="EOD square-off", ts=ts), trade_date)

    def square_off_all(self) -> None:
        now = datetime.now()
        today = now.date().isoformat()
        for symbol, (side, quantity, entry_price) in list(self.open_positions.items()):
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
    from strategy.scanner import compute_narrow_range_days, group_candles_by_day, rank_candidates, scan_backtest_day

    reset_backtest_data()

    auth = KiteAuth()
    kite = auth.authenticated_client()

    strategy = ORBStrategy(
        settings.orb_range_minutes,
        settings.orb_candle_interval,
        settings.orb_volume_multiplier,
        settings.orb_volume_lookback,
        settings.orb_nr7_lookback,
        settings.orb_entry_cutoff_time,
        settings.orb_target_r,
    )
    risk_manager = RiskManager(
        settings.capital,
        settings.risk_pct_per_trade,
        settings.daily_loss_cap_pct,
        settings.max_consecutive_errors,
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
            for candle in tradable_candles:
                app.handle_candle(symbol, {**candle, "_narrow_range_day": is_narrow_range_day})
            app.square_off_symbol(symbol, tradable_candles[-1]["close"], day, ts=tradable_candles[-1]["date"])

        for symbol, by_day in candles_by_symbol_day.items():
            day_candles = by_day.get(day)
            if day_candles:
                last_close_per_symbol[symbol] = day_candles[-1]["close"]

    print_report("backtest", app.open_positions)


def run_live_or_paper() -> None:
    from auth.kite_auth import KiteAuth
    from data.candle_builder import CandleBuilder
    from data.ticker import LiveTicker
    from risk.risk_manager import RiskManager
    from strategy.scanner import rank_candidates, scan_live_universe

    strategy = ORBStrategy(
        settings.orb_range_minutes,
        settings.orb_candle_interval,
        settings.orb_volume_multiplier,
        settings.orb_volume_lookback,
        settings.orb_nr7_lookback,
        settings.orb_entry_cutoff_time,
        settings.orb_target_r,
    )
    risk_manager = RiskManager(
        settings.capital,
        settings.risk_pct_per_trade,
        settings.daily_loss_cap_pct,
        settings.max_consecutive_errors,
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
        run_backtest()
    else:
        run_live_or_paper()


if __name__ == "__main__":
    main()
