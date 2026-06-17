import os
import tempfile

import pytest


@pytest.fixture
def risk_manager(monkeypatch):
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    monkeypatch.setenv("DB_PATH", db_path)

    import storage.db as db_module

    db_module._connection = None
    monkeypatch.setattr("storage.db.settings.db_path", db_path)

    from risk.risk_manager import RiskManager

    yield RiskManager(
        capital=100000,
        risk_pct_per_trade=1.0,
        daily_loss_cap_pct=3.0,
        max_consecutive_errors=3,
    )

    os.remove(db_path)


def test_position_size(risk_manager):
    # risk amount = 1% of 100000 = 1000; per-share risk = 1 (100 -> 99)
    qty = risk_manager.position_size(entry_price=100, stop_loss_price=99)
    assert qty == 1000


def test_position_size_zero_risk_returns_zero(risk_manager):
    assert risk_manager.position_size(entry_price=100, stop_loss_price=100) == 0


def test_daily_loss_cap_halts_trading(risk_manager):
    assert risk_manager.approve_signal() is True
    risk_manager.record_pnl(-3500)  # exceeds 3% of 100000 = 3000
    assert risk_manager.approve_signal() is False


def test_kill_switch_trips_after_max_errors(risk_manager):
    from risk.risk_manager import KillSwitchTripped

    risk_manager.record_error("test", "err1")
    risk_manager.record_error("test", "err2")
    with pytest.raises(KillSwitchTripped):
        risk_manager.record_error("test", "err3")
    assert risk_manager.is_killed is True
    assert risk_manager.approve_signal() is False
