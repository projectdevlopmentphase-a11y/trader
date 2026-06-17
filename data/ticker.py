"""Thin wrapper around KiteTicker for live/paper modes.

Feeds every tick into a CandleBuilder; downstream code only ever deals with
completed candles via the builder's callback, never raw ticks.
"""
from __future__ import annotations

from typing import Callable

from kiteconnect import KiteTicker

from data.candle_builder import CandleBuilder
from storage.db import log_error


class LiveTicker:
    def __init__(
        self,
        api_key: str,
        access_token: str,
        instrument_tokens: list[int],
        token_to_symbol: dict[int, str],
        candle_builder: CandleBuilder,
        on_error: Callable[[str], None] | None = None,
    ):
        self.instrument_tokens = instrument_tokens
        self.token_to_symbol = token_to_symbol
        self.candle_builder = candle_builder
        self.on_error = on_error or (lambda msg: log_error("ticker", msg))
        self.kws = KiteTicker(api_key, access_token)

        self.kws.on_ticks = self._on_ticks
        self.kws.on_connect = self._on_connect
        self.kws.on_close = self._on_close
        self.kws.on_error = self._on_error

    def _on_connect(self, ws, response) -> None:
        ws.subscribe(self.instrument_tokens)
        ws.set_mode(ws.MODE_FULL, self.instrument_tokens)

    def _on_ticks(self, ws, ticks: list[dict]) -> None:
        for tick in ticks:
            symbol = self.token_to_symbol.get(tick["instrument_token"])
            if symbol is None:
                continue
            price = tick.get("last_price")
            volume = tick.get("volume_traded", 0)
            if price is not None:
                self.candle_builder.on_tick(symbol, price, volume)

    def _on_close(self, ws, code, reason) -> None:
        self.on_error(f"ticker closed: {code} {reason}")

    def _on_error(self, ws, code, reason) -> None:
        self.on_error(f"ticker error: {code} {reason}")

    def start(self, threaded: bool = True) -> None:
        self.kws.connect(threaded=threaded)

    def stop(self) -> None:
        self.kws.close()
