"""Main orchestrator: wires config, data, strategy, risk and execution
together for whichever mode is configured, and registers the EOD square-off.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from config.settings import settings
from execution.backtest import BacktestExecutor
from execution.base import OrderExecutor
from execution.live import LiveExecutor
from execution.paper import PaperExecutor
from scheduler.scheduler import start_square_off_scheduler
from storage.db import log_error, log_signal
from strategy.base import Action, Signal
from strategy.orb import ORBStrategy


class TraderApp:
    def __init__(self, strategy, executor: OrderExecutor, risk_manager):
        self.strategy = strategy
        self.executor = executor
        self.risk_manager = risk_manager
        # symbol -> (action, quantity, entry_price)
        self.open_positions: dict[str, tuple[str, int, float]] = {}

    def handle_candle(self, symbol: str, candle: dict) -> None:
        signal = self.strategy.on_candle(symbol, candle)
        if signal is None:
            return
        self._handle_signal(signal)

    def _handle_signal(self, signal: Signal) -> None:
        log_signal(signal.strategy, signal.symbol, signal.action.value, signal.price, signal.reason)

        if not self.risk_manager.approve_signal():
            return

        if signal.action == Action.EXIT:
            self._exit_position(signal)
            return

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

    def _exit_position(self, signal: Signal) -> None:
        position = self.open_positions.pop(signal.symbol, None)
        if position is None:
            return
        side, quantity, entry_price = position
        exit_side = Action.SELL if side == "BUY" else Action.BUY
        exit_signal = Signal(signal.strategy, signal.symbol, exit_side, signal.price, reason=signal.reason)

        try:
            fill = self.executor.execute(exit_signal, quantity)
        except Exception as exc:
            self.risk_manager.record_error("executor", str(exc))
            return

        self.risk_manager.record_success()
        pnl = (fill.price - entry_price) * quantity if side == "BUY" else (entry_price - fill.price) * quantity
        self.risk_manager.record_pnl(pnl)

    def square_off_all(self) -> None:
        for symbol, (side, quantity, entry_price) in list(self.open_positions.items()):
            self._exit_position(Signal(self.strategy.name, symbol, Action.EXIT, entry_price, reason="EOD square-off"))


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

    auth = KiteAuth()
    kite = auth.authenticated_client()

    strategy = ORBStrategy(settings.orb_range_minutes, settings.orb_candle_interval)
    risk_manager = RiskManager(
        settings.capital,
        settings.risk_pct_per_trade,
        settings.daily_loss_cap_pct,
        settings.max_consecutive_errors,
    )
    app = TraderApp(strategy, BacktestExecutor(), risk_manager)

    instruments = kite.instruments("NSE")
    symbol_to_token = {i["tradingsymbol"]: i["instrument_token"] for i in instruments}

    to_date = datetime.now()
    from_date = to_date - timedelta(days=30)

    for symbol in settings.watchlist:
        token = symbol_to_token.get(symbol)
        if token is None:
            log_error("backtest", f"unknown symbol {symbol}")
            continue
        candles = fetch_historical_candles(kite, token, from_date, to_date, settings.orb_candle_interval)
        for candle in candles:
            app.handle_candle(symbol, candle)


def run_live_or_paper() -> None:
    from auth.kite_auth import KiteAuth
    from data.candle_builder import CandleBuilder
    from data.ticker import LiveTicker
    from risk.risk_manager import RiskManager

    strategy = ORBStrategy(settings.orb_range_minutes, settings.orb_candle_interval)
    risk_manager = RiskManager(
        settings.capital,
        settings.risk_pct_per_trade,
        settings.daily_loss_cap_pct,
        settings.max_consecutive_errors,
    )
    executor = build_executor(settings.mode)
    app = TraderApp(strategy, executor, risk_manager)

    start_square_off_scheduler(app.square_off_all)

    auth = KiteAuth()
    kite = auth.authenticated_client()
    instruments = kite.instruments("NSE")
    symbol_to_token = {i["tradingsymbol"]: i["instrument_token"] for i in instruments if i["tradingsymbol"] in settings.watchlist}
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


def main() -> None:
    if settings.mode == "backtest":
        run_backtest()
    else:
        run_live_or_paper()


if __name__ == "__main__":
    main()
