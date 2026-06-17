# trader

Personal equity intraday algo trading bot for Zerodha Kite Connect, built
around a pluggable strategy engine. Ships with Opening Range Breakout (ORB);
other strategies (VWAP, EMA crossover, Supertrend) can be added by
implementing `strategy.base.Strategy` without touching anything else.

Personal/family use only. See compliance notes below.

## Architecture

1. **Config & secrets** (`config/`) — loaded from `.env`, never hardcoded.
2. **Auth** (`auth/`) — daily Kite OAuth login flow; access tokens are not
   persistent and must be regenerated each trading day.
3. **Data** (`data/`) — historical candles for backtesting, KiteTicker
   websocket + candle builder for paper/live.
4. **Strategy engine** (`strategy/`) — common `on_candle(symbol, candle) ->
   Signal | None` interface. ORB is the first implementation.
5. **Risk manager** (`risk/`) — position sizing by % risk per trade, daily
   loss cap, and a kill switch after repeated errors.
6. **Order executor** (`execution/`) — same interface across backtest,
   paper and live modes.

SQLite (`storage/db.py`) logs every signal, trade, error, and the running
daily P&L. `scheduler/scheduler.py` registers the mandatory EOD square-off.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env  # fill in KITE_API_KEY / KITE_API_SECRET / watchlist / risk params
```

## Daily login

Required every trading day — access tokens expire daily and there is no
refresh token:

```bash
.venv/bin/python -m auth.kite_auth
```

Follow the printed login URL, paste back the `request_token` from the
redirect, and the access token is written into `.env` for the day.

## Running

Set `MODE` in `.env` to `backtest`, `paper`, or `live`, then:

```bash
.venv/bin/python main.py
```

Always clear backtest, then a real-time paper run, before allowing a
strategy to place real orders in live mode.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

## Deployment

The droplet pulls this repo and runs the bot via the systemd unit in
`deploy/trader.service` (copy to `/etc/systemd/system/trader.service`,
adjust the `User`/`WorkingDirectory` paths, then `systemctl enable --now
trader`). Only live order placement requires the droplet's whitelisted
static IP — backtest/paper can run anywhere.

## Compliance notes (SEBI framework, effective 1 Apr 2026)

- Static IP must stay whitelisted in the Kite developer console for live
  order placement.
- OAuth + 2FA login is required every session; access tokens expire daily.
- Market orders require a non-zero `market_protection` value
  (`MARKET_PROTECTION_PCT` in `.env`) or they are rejected.
- Order rate must stay under 10/sec to avoid formal exchange strategy
  registration.
- For personal/family use only — do not share access outside immediate
  family (spouse, dependent children, dependent parents).
