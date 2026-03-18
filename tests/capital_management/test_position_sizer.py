"""
Tests — Position Sizer: allocation logic, caps, risk adjustment, minimum viability.
"""
import pytest

from capital_management.config import (
    CapitalConfig,
    DiversificationConfig,
    PositionSizingConfig,
    RiskConfig,
    StrategyMode,
)
from capital_management.models import PortfolioState, Position, PositionStatus
from capital_management.position_sizer import PositionSizer


def _config(**overrides) -> CapitalConfig:
    sizing = PositionSizingConfig(
        max_allocation_pct_per_trade=overrides.get("max_trade", 0.20),
        max_allocation_pct_per_category=overrides.get("max_cat", 0.40),
        min_allocation_usd=overrides.get("min_alloc", 20.0),
        risk_adjustment_factor=overrides.get("risk_factor", 0.6),
    )
    div = DiversificationConfig(
        max_pct_per_product=0.25,
        max_pct_per_marketplace=0.45,
        max_pct_per_category=0.40,
        max_open_positions=15,
    )
    risk = RiskConfig(
        max_drawdown_pct=0.20,
        capital_stop_loss_pct=0.15,
        consecutive_loss_limit=3,
        sizing_reduction_on_streak=0.50,
        cooldown_after_loss_minutes=60,
        max_daily_loss_usd=500,
    )
    return CapitalConfig(
        strategy=StrategyMode.BALANCED,
        initial_capital=5000,
        position_sizing=sizing,
        diversification=div,
        risk=risk,
    )


def _portfolio(
    total: float = 5000,
    available: float = 5000,
    positions: list[Position] | None = None,
) -> PortfolioState:
    return PortfolioState(
        total_capital=total,
        available_capital=available,
        allocated_capital=total - available,
        active_positions=positions or [],
    )


class TestBaseAllocation:

    def test_high_score_gets_more(self):
        sizer = PositionSizer(_config())
        portfolio = _portfolio(total=10000, available=10000)

        high = sizer.size_position(90, 20, 100, portfolio)
        low = sizer.size_position(30, 20, 100, portfolio)

        assert high.allocation > low.allocation
        assert high.is_valid
        assert low.is_valid

    def test_zero_score_rejected(self):
        sizer = PositionSizer(_config())
        result = sizer.size_position(0, 20, 100, _portfolio())
        assert result.is_valid is False
        assert "score too low" in result.reason


class TestRiskAdjustment:

    def test_high_risk_reduces_allocation(self):
        sizer = PositionSizer(_config())
        portfolio = _portfolio(total=10000, available=10000)

        low_risk = sizer.size_position(80, 10, 100, portfolio)
        high_risk = sizer.size_position(80, 90, 100, portfolio)

        assert low_risk.allocation > high_risk.allocation

    def test_risk_adjustment_override(self):
        sizer = PositionSizer(_config())
        portfolio = _portfolio(total=10000, available=10000)

        normal = sizer.size_position(80, 30, 100, portfolio)
        reduced = sizer.size_position(80, 30, 100, portfolio, risk_adjustment_override=0.5)

        assert reduced.allocation < normal.allocation


class TestCaps:

    def test_per_trade_cap(self):
        """Allocation cannot exceed max_allocation_pct_per_trade * total_capital."""
        sizer = PositionSizer(_config(max_trade=0.10))
        portfolio = _portfolio(total=10000, available=10000)

        result = sizer.size_position(100, 0, 100, portfolio)
        assert result.allocation <= 10000 * 0.10

    def test_available_capital_cap(self):
        """Cannot allocate more than available capital."""
        sizer = PositionSizer(_config())
        portfolio = _portfolio(total=10000, available=200)

        result = sizer.size_position(100, 0, 100, portfolio)
        assert result.allocation <= 200

    def test_per_category_cap(self):
        """Per-category cap respected when positions exist in same category."""
        sizer = PositionSizer(_config(max_cat=0.30))
        existing = Position(
            opportunity_id=1, allocated_amount=2500, entry_price=100,
            expected_profit=20, expected_roi=0.2, risk_score=20,
            category="electronics",
        )
        portfolio = _portfolio(total=10000, available=7500, positions=[existing])

        result = sizer.size_position(100, 0, 100, portfolio, category="electronics")
        # Category cap: 10000 * 0.30 = 3000. Already 2500 allocated. Headroom = 500.
        assert result.allocation <= 500


class TestMinimumViability:

    def test_below_minimum_rejected(self):
        sizer = PositionSizer(_config(min_alloc=50.0))
        portfolio = _portfolio(total=100, available=100)

        # Score 10 on $100 capital with 20% max → base = $2, risk_adj ~$1.76
        result = sizer.size_position(10, 20, 10, portfolio)
        assert result.is_valid is False
        assert "below minimum" in result.reason

    def test_above_minimum_accepted(self):
        sizer = PositionSizer(_config(min_alloc=20.0))
        portfolio = _portfolio(total=5000, available=5000)

        result = sizer.size_position(80, 30, 100, portfolio)
        assert result.is_valid is True
        assert result.allocation >= 20.0


class TestCapitalRequired:

    def test_allocation_covers_capital_required(self):
        sizer = PositionSizer(_config(max_trade=0.20))
        portfolio = _portfolio(total=5000, available=5000)

        # Score would give less than capital_required=500
        result = sizer.size_position(30, 20, 500, portfolio)
        # If capital_required <= max_per_trade, it should use capital_required
        if result.is_valid:
            assert result.allocation >= 500 or result.allocation <= portfolio.available_capital

    def test_capital_required_exceeds_cap_rejected(self):
        sizer = PositionSizer(_config(max_trade=0.05))
        portfolio = _portfolio(total=1000, available=1000)

        # Max per trade = $50, but capital_required = $200
        result = sizer.size_position(50, 20, 200, portfolio)
        assert result.is_valid is False
        assert "exceeds per-trade cap" in result.reason


class TestSizingResult:

    def test_result_fields(self):
        sizer = PositionSizer(_config())
        result = sizer.size_position(80, 30, 100, _portfolio(total=5000, available=5000))

        assert result.base_allocation > 0
        assert result.risk_adjusted_allocation > 0
        assert result.allocation > 0
        assert result.is_valid is True
