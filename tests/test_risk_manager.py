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


def test_pairs_position_size_uses_notional_and_scales_by_hedge_ratio(risk_manager):
    # total_notional = 10% of 100000 = 10000; combined leg cost per unit =
    # price_a + hedge_ratio * price_b = 100 + 2*50 = 200 -> qty_a = 50, qty_b = 100
    qty_a, qty_b = risk_manager.pairs_position_size(price_a=100, price_b=50, hedge_ratio=2.0)
    assert qty_a == 50
    assert qty_b == 100


def test_pairs_position_size_zero_price_returns_zero(risk_manager):
    assert risk_manager.pairs_position_size(price_a=0, price_b=50, hedge_ratio=1.0) == (0, 0)


def test_pairs_position_size_splits_capital_pct_across_n_pairs():
    from risk.risk_manager import RiskManager

    # Same setup as test_pairs_position_size_uses_notional_and_scales_by_hedge_ratio,
    # but with 2 configured pairs the 10% capital_pct should be halved to 5%:
    # total_notional = 5% of 100000 = 5000 -> qty_a = 25, qty_b = 50.
    risk_manager = RiskManager(
        capital=100000,
        risk_pct_per_trade=1.0,
        daily_loss_cap_pct=3.0,
        max_consecutive_errors=3,
        n_pairs=2,
    )
    qty_a, qty_b = risk_manager.pairs_position_size(price_a=100, price_b=50, hedge_ratio=2.0)
    assert qty_a == 25
    assert qty_b == 50


def test_daily_loss_cap_halts_trading(risk_manager):
    assert risk_manager.approve_signal("2026-06-18") is True
    risk_manager.record_pnl("2026-06-18", -3500)  # exceeds 3% of 100000 = 3000
    assert risk_manager.approve_signal("2026-06-18") is False


def test_kill_switch_trips_after_max_errors(risk_manager):
    from risk.risk_manager import KillSwitchTripped

    risk_manager.record_error("test", "err1")
    risk_manager.record_error("test", "err2")
    with pytest.raises(KillSwitchTripped):
        risk_manager.record_error("test", "err3")
    assert risk_manager.is_killed is True
    assert risk_manager.approve_signal("2026-06-18") is False
