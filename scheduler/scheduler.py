"""Schedules the mandatory EOD square-off.

The daily OAuth login is interactive (auth.kite_auth.run_interactive_login)
and is run manually before market open, not on this scheduler. This
scheduler only handles the unattended EOD square-off: even though MIS
positions auto-square-off at the exchange, the bot exits its own open
positions cleanly beforehand so fills/P&L are recorded under its control.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler

from config.settings import settings


def start_square_off_scheduler(square_off_fn: Callable[[], None]) -> BackgroundScheduler:
    hour, minute = (int(part) for part in settings.square_off_time.split(":"))
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        square_off_fn,
        "cron",
        day_of_week="mon-fri",
        hour=hour,
        minute=minute,
        id="eod_square_off",
    )
    scheduler.start()
    return scheduler
