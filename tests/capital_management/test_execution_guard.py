"""
Tests — Execution Guard: final gate aggregating risk, sizing, diversification.
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
from capital_management.models import PortfolioState, Position, PositionStatus
from capital_management.execution_guard import ExecutionGuard


def _config(**overrides) -> CapitalConfig:
    return CapitalConfig(
        strategy=StrategyMode.BALANCED,
        initial_capital=10000,
        position_sizing=PositionSizingConfig(
            max_allocation_pct_per_trade=overrides.get("max_trade", 0.20),
            max_allocation_pct_per_category=0.40,
            min_allocation_usd=overrides.get("min_alloc", 20.0),
            risk_adjustment_factor=0.6,
        ),
        diversification=DiversificationConfig(
            max_pct_per_product=0.25,
            max_pct_per_marketplace=0.45,
            max_pct_per_category=0.40,
            max_open_positions=overrides.get("max_positions", 15),
        ),
        risk=RiskConfig(
            max_drawdown_pct=overrides.get("max_dd", 0.20),
            capital_stop_loss_pct=0.15,
            consecutive_loss_limit=overrides.get("loss_limit", 3),
            sizing_reduction_on_streak=0.50,
            cooldown_after_loss_minutes=overrides.get("cooldown", 60),
            max_daily_loss_usd=500,
        ),
    )


def _portfolio(**overrides) -> PortfolioState:
    defaults = dict(
        total_capital=10000,
        available_capital=8000,
        peak_capital=10000,
        drawdown_pct=0.0,
        consecutive_losses=0,
        active_positions=[],
    )
    defaults.update(overrides)
    return PortfolioState(**defaults)


class TestFullApproval:

    def test_good_opportunity_approved(self):
        guard = ExecutionGuard(_config())
        result = guard.evaluate(
            opportunity_id=1,
            opportunity_score=80,
            risk_score=25,
            capital_required=200,
            expected_profit=40,
            expected_roi=0.20,
            portfolio=_portfolio(),
        )
        assert result.approved is True
        assert result.final_allocation > 0
        assert result.risk_check is not None
        assert result.sizing_result is not None
        assert result.diversification_result is not None

    def test_returns_sub_results(self):
        guard = ExecutionGuard(_config())
        result = guard.evaluate(
            opportunity_id=1,
            opportunity_score=80,
            risk_score=25,
            capital_required=200,
            expected_profit=40,
            expected_roi=0.20,
            portfolio=_portfolio(),
        )
        assert result.risk_check.can_execute is True
        assert result.sizing_result.is_valid is True
        assert result.diversification_result.is_safe is True


class TestRiskGateBlocks:

    def test_drawdown_blocks(self):
        guard = ExecutionGuard(_config(max_dd=0.10))
        result = guard.evaluate(
            opportunity_id=1,
            opportunity_score=80,
            risk_score=25,
            capital_required=200,
            expected_profit=40,
            expected_roi=0.20,
            portfolio=_portfolio(drawdown_pct=0.15),
        )
        assert result.approved is False
        assert any("drawdown" in r.lower() for r in result.rejection_reasons)

    def test_cooldown_blocks(self):
        guard = ExecutionGuard(_config(cooldown=60))
        recent_loss = datetime.now(timezone.utc) - timedelta(minutes=10)
        result = guard.evaluate(
            opportunity_id=1,
            opportunity_score=80,
            risk_score=25,
            capital_required=200,
            expected_profit=40,
            expected_roi=0.20,
            portfolio=_portfolio(last_loss_at=recent_loss),
        )
        assert result.approved is False
        assert any("cooldown" in r.lower() for r in result.rejection_reasons)


class TestSizingGateBlocks:

    def test_low_score_rejected(self):
        guard = ExecutionGuard(_config(min_alloc=50.0))
        # Very low score on small capital → sizing below minimum
        result = guard.evaluate(
            opportunity_id=1,
            opportunity_score=5,
            risk_score=50,
            capital_required=10,
            expected_profit=2,
            expected_roi=0.05,
            portfolio=_portfolio(total_capital=500, available_capital=500, peak_capital=500),
        )
        assert result.approved is False
        assert any("sizing" in r for r in result.rejection_reasons)


class TestDiversificationGateBlocks:

    def test_max_positions_blocks(self):
        guard = ExecutionGuard(_config(max_positions=2))
        existing = [
            Position(
                opportunity_id=i, allocated_amount=500, entry_price=100,
                expected_profit=20, expected_roi=0.2, risk_score=20,
            )
            for i in range(2)
        ]
        result = guard.evaluate(
            opportunity_id=99,
            opportunity_score=80,
            risk_score=25,
            capital_required=200,
            expected_profit=40,
            expected_roi=0.20,
            portfolio=_portfolio(active_positions=existing),
        )
        assert result.approved is False
        assert any("max_open_positions" in r for r in result.rejection_reasons)

    def test_product_concentration_blocks(self):
        guard = ExecutionGuard(_config())
        # Existing positions in same product take up 24% (just under 25% limit)
        existing = Position(
            opportunity_id=1, allocated_amount=2400, entry_price=2400,
            expected_profit=200, expected_roi=0.08, risk_score=20,
            product_id=42,
        )
        # New position would push to 2400+800=3200/10000=32% > 25%
        result = guard.evaluate(
            opportunity_id=2,
            opportunity_score=90,
            risk_score=10,
            capital_required=800,
            expected_profit=100,
            expected_roi=0.125,
            portfolio=_portfolio(
                active_positions=[existing],
                allocated_capital=2400,
                available_capital=7600,
            ),
            product_id=42,
        )
        assert result.approved is False
        assert any("product_concentration" in r for r in result.rejection_reasons)


class TestCapitalAvailability:

    def test_insufficient_capital_rejected(self):
        guard = ExecutionGuard(_config())
        result = guard.evaluate(
            opportunity_id=1,
            opportunity_score=80,
            risk_score=25,
            capital_required=200,
            expected_profit=40,
            expected_roi=0.20,
            portfolio=_portfolio(available_capital=5),
        )
        assert result.approved is False


class TestConsecutiveLossesReduceSizing:

    def test_streak_reduces_allocation(self):
        guard = ExecutionGuard(_config(loss_limit=3))
        normal_result = guard.evaluate(
            opportunity_id=1,
            opportunity_score=80,
            risk_score=25,
            capital_required=100,
            expected_profit=30,
            expected_roi=0.30,
            portfolio=_portfolio(consecutive_losses=0),
        )
        reduced_result = guard.evaluate(
            opportunity_id=2,
            opportunity_score=80,
            risk_score=25,
            capital_required=100,
            expected_profit=30,
            expected_roi=0.30,
            portfolio=_portfolio(consecutive_losses=5),
        )
        assert normal_result.approved is True
        assert reduced_result.approved is True
        assert reduced_result.final_allocation < normal_result.final_allocation
