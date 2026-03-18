"""
Tests — Risk Manager: circuit breakers, consecutive losses, cooldown, daily limits.
"""
import pytest
from datetime import datetime, timedelta, timezone

from capital_management.config import (
    CapitalConfig,
    DiversificationConfig,
    PositionSizingConfig,
    RiskConfig,
    StrategyMode,
)
from capital_management.models import PortfolioState, RiskLevel
from capital_management.risk_manager import RiskManager


def _risk_cfg(**overrides) -> CapitalConfig:
    risk = RiskConfig(
        max_drawdown_pct=overrides.get("max_dd", 0.20),
        capital_stop_loss_pct=overrides.get("stop_loss", 0.15),
        consecutive_loss_limit=overrides.get("loss_limit", 3),
        sizing_reduction_on_streak=overrides.get("reduction", 0.50),
        cooldown_after_loss_minutes=overrides.get("cooldown", 60),
        max_daily_loss_usd=overrides.get("daily_cap", 500),
    )
    return CapitalConfig(
        strategy=StrategyMode.BALANCED,
        initial_capital=10000,
        position_sizing=PositionSizingConfig(0.20, 0.40, 20, 0.6),
        diversification=DiversificationConfig(0.25, 0.45, 0.40, 15),
        risk=risk,
    )


def _portfolio(**overrides) -> PortfolioState:
    defaults = dict(
        total_capital=10000,
        available_capital=8000,
        peak_capital=10000,
        drawdown_pct=0.0,
        consecutive_losses=0,
        daily_loss_usd=0.0,
    )
    defaults.update(overrides)
    return PortfolioState(**defaults)


class TestDrawdownHalt:

    def test_below_limit_allows(self):
        mgr = RiskManager(_risk_cfg(max_dd=0.20))
        result = mgr.check(_portfolio(drawdown_pct=0.10))
        assert result.can_execute is True

    def test_at_limit_halts(self):
        mgr = RiskManager(_risk_cfg(max_dd=0.20))
        result = mgr.check(_portfolio(drawdown_pct=0.20))
        assert result.can_execute is False
        assert any("drawdown" in r for r in result.reasons)
        assert result.risk_level == RiskLevel.CRITICAL

    def test_above_limit_halts(self):
        mgr = RiskManager(_risk_cfg(max_dd=0.20))
        result = mgr.check(_portfolio(drawdown_pct=0.25))
        assert result.can_execute is False


class TestCapitalStopLoss:

    def test_below_stop_loss_allows(self):
        mgr = RiskManager(_risk_cfg(stop_loss=0.15))
        # Peak 10000, current 9000 → loss 10% < 15%
        result = mgr.check(_portfolio(total_capital=9000, peak_capital=10000))
        assert result.can_execute is True

    def test_at_stop_loss_halts(self):
        mgr = RiskManager(_risk_cfg(stop_loss=0.15))
        # Peak 10000, current 8500 → loss 15%
        result = mgr.check(_portfolio(total_capital=8500, peak_capital=10000))
        assert result.can_execute is False
        assert any("capital loss" in r for r in result.reasons)


class TestConsecutiveLosses:

    def test_below_limit_no_reduction(self):
        mgr = RiskManager(_risk_cfg(loss_limit=3))
        result = mgr.check(_portfolio(consecutive_losses=2))
        assert result.can_execute is True
        assert result.adjustment_factor == 1.0

    def test_at_limit_reduces_sizing(self):
        mgr = RiskManager(_risk_cfg(loss_limit=3, reduction=0.50))
        result = mgr.check(_portfolio(consecutive_losses=3))
        assert result.can_execute is True  # Not a halt, just reduction
        assert result.adjustment_factor == 0.50
        assert any("consecutive losses" in r.lower() for r in result.reasons)

    def test_above_limit_also_reduces(self):
        mgr = RiskManager(_risk_cfg(loss_limit=3, reduction=0.50))
        result = mgr.check(_portfolio(consecutive_losses=7))
        assert result.adjustment_factor == 0.50


class TestCooldown:

    def test_no_cooldown_when_no_loss(self):
        mgr = RiskManager(_risk_cfg(cooldown=60))
        result = mgr.check(_portfolio(last_loss_at=None))
        assert result.can_execute is True

    def test_in_cooldown_blocks(self):
        mgr = RiskManager(_risk_cfg(cooldown=60))
        recent_loss = datetime.now(timezone.utc) - timedelta(minutes=30)
        result = mgr.check(_portfolio(last_loss_at=recent_loss))
        assert result.can_execute is False
        assert any("cooldown" in r.lower() for r in result.reasons)

    def test_after_cooldown_allows(self):
        mgr = RiskManager(_risk_cfg(cooldown=60))
        old_loss = datetime.now(timezone.utc) - timedelta(minutes=90)
        result = mgr.check(_portfolio(last_loss_at=old_loss))
        assert result.can_execute is True


class TestDailyLossCap:

    def test_below_cap_allows(self):
        mgr = RiskManager(_risk_cfg(daily_cap=500))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        result = mgr.check(_portfolio(daily_loss_usd=300, daily_loss_reset_date=today))
        assert result.can_execute is True

    def test_at_cap_halts(self):
        mgr = RiskManager(_risk_cfg(daily_cap=500))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        result = mgr.check(_portfolio(daily_loss_usd=500, daily_loss_reset_date=today))
        assert result.can_execute is False
        assert any("daily loss" in r for r in result.reasons)

    def test_yesterday_loss_doesnt_count(self):
        mgr = RiskManager(_risk_cfg(daily_cap=500))
        result = mgr.check(_portfolio(daily_loss_usd=999, daily_loss_reset_date="2020-01-01"))
        assert result.can_execute is True


class TestRiskLevel:

    def test_healthy_portfolio_is_low(self):
        mgr = RiskManager(_risk_cfg())
        result = mgr.check(_portfolio())
        assert result.risk_level == RiskLevel.LOW

    def test_halted_is_critical(self):
        mgr = RiskManager(_risk_cfg(max_dd=0.10))
        result = mgr.check(_portfolio(drawdown_pct=0.15))
        assert result.risk_level == RiskLevel.CRITICAL

    def test_moderate_drawdown_is_medium(self):
        mgr = RiskManager(_risk_cfg(max_dd=0.20))
        # 12% drawdown > 20% * 0.5 = 10%
        result = mgr.check(_portfolio(drawdown_pct=0.12))
        assert result.risk_level == RiskLevel.MEDIUM


class TestMultipleBreakers:

    def test_multiple_violations_all_reported(self):
        mgr = RiskManager(_risk_cfg(max_dd=0.10, daily_cap=100))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        result = mgr.check(_portfolio(
            drawdown_pct=0.15,
            daily_loss_usd=200,
            daily_loss_reset_date=today,
        ))
        assert result.can_execute is False
        assert len(result.reasons) >= 2
