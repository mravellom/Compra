"""
Tests — Diversification Guard: concentration limits per product, marketplace, category.
"""
import pytest
from datetime import datetime, timezone

from capital_management.config import (
    CapitalConfig,
    DiversificationConfig,
    PositionSizingConfig,
    RiskConfig,
    StrategyMode,
)
from capital_management.models import PortfolioState, Position, PositionStatus
from capital_management.diversification import DiversificationGuard


def _config(
    max_product: float = 0.25,
    max_marketplace: float = 0.45,
    max_category: float = 0.40,
    max_positions: int = 15,
) -> CapitalConfig:
    return CapitalConfig(
        strategy=StrategyMode.BALANCED,
        initial_capital=10000,
        position_sizing=PositionSizingConfig(0.20, 0.40, 20, 0.6),
        diversification=DiversificationConfig(
            max_pct_per_product=max_product,
            max_pct_per_marketplace=max_marketplace,
            max_pct_per_category=max_category,
            max_open_positions=max_positions,
        ),
        risk=RiskConfig(0.20, 0.15, 3, 0.50, 60, 500),
    )


def _position(
    opp_id: int = 1,
    amount: float = 500,
    product_id: int = 100,
    marketplace: str = "amazon",
    category: str = "electronics",
) -> Position:
    return Position(
        opportunity_id=opp_id,
        allocated_amount=amount,
        entry_price=amount,
        expected_profit=50,
        expected_roi=0.10,
        risk_score=30,
        product_id=product_id,
        marketplace=marketplace,
        category=category,
    )


def _portfolio(
    total: float = 10000,
    positions: list[Position] | None = None,
) -> PortfolioState:
    pos = positions or []
    allocated = sum(p.allocated_amount for p in pos if p.status == PositionStatus.OPEN)
    return PortfolioState(
        total_capital=total,
        available_capital=total - allocated,
        allocated_capital=allocated,
        active_positions=pos,
    )


class TestMaxOpenPositions:

    def test_under_limit_passes(self):
        guard = DiversificationGuard(_config(max_positions=5))
        portfolio = _portfolio(positions=[_position(opp_id=i) for i in range(3)])
        result = guard.check(_position(opp_id=99), portfolio)
        assert result.is_safe is True

    def test_at_limit_fails(self):
        guard = DiversificationGuard(_config(max_positions=3))
        portfolio = _portfolio(positions=[_position(opp_id=i) for i in range(3)])
        result = guard.check(_position(opp_id=99), portfolio)
        assert result.is_safe is False
        assert any("max_open_positions" in v for v in result.violations)


class TestProductConcentration:

    def test_below_limit_passes(self):
        guard = DiversificationGuard(_config(max_product=0.25))
        existing = _position(opp_id=1, amount=1000, product_id=42)
        new_pos = _position(opp_id=2, amount=500, product_id=42)
        portfolio = _portfolio(total=10000, positions=[existing])

        result = guard.check(new_pos, portfolio)
        # 1500/10000 = 15% < 25%
        assert result.is_safe is True

    def test_above_limit_fails(self):
        guard = DiversificationGuard(_config(max_product=0.15))
        existing = _position(opp_id=1, amount=1000, product_id=42)
        new_pos = _position(opp_id=2, amount=1000, product_id=42)
        portfolio = _portfolio(total=10000, positions=[existing])

        result = guard.check(new_pos, portfolio)
        # 2000/10000 = 20% > 15%
        assert result.is_safe is False
        assert any("product_concentration" in v for v in result.violations)

    def test_different_products_independent(self):
        guard = DiversificationGuard(_config(max_product=0.15))
        existing = _position(opp_id=1, amount=1400, product_id=42)
        new_pos = _position(opp_id=2, amount=1400, product_id=99)
        portfolio = _portfolio(total=10000, positions=[existing])

        result = guard.check(new_pos, portfolio)
        assert result.is_safe is True


class TestMarketplaceConcentration:

    def test_below_limit_passes(self):
        guard = DiversificationGuard(_config(max_marketplace=0.45))
        existing = _position(opp_id=1, amount=2000, marketplace="amazon", product_id=10)
        new_pos = _position(opp_id=2, amount=1000, marketplace="amazon", product_id=20)
        portfolio = _portfolio(total=10000, positions=[existing])

        result = guard.check(new_pos, portfolio)
        # 3000/10000 = 30% < 45%
        assert result.is_safe is True

    def test_above_limit_fails(self):
        guard = DiversificationGuard(_config(max_marketplace=0.30))
        existing = _position(opp_id=1, amount=2500, marketplace="ebay")
        new_pos = _position(opp_id=2, amount=1000, marketplace="ebay")
        portfolio = _portfolio(total=10000, positions=[existing])

        result = guard.check(new_pos, portfolio)
        # 3500/10000 = 35% > 30%
        assert result.is_safe is False
        assert any("marketplace_concentration" in v for v in result.violations)


class TestCategoryConcentration:

    def test_above_limit_fails(self):
        guard = DiversificationGuard(_config(max_category=0.30))
        existing = _position(opp_id=1, amount=2500, category="phones")
        new_pos = _position(opp_id=2, amount=1000, category="phones")
        portfolio = _portfolio(total=10000, positions=[existing])

        result = guard.check(new_pos, portfolio)
        # 3500/10000 = 35% > 30%
        assert result.is_safe is False
        assert any("category_concentration" in v for v in result.violations)


class TestMultipleViolations:

    def test_all_violations_collected(self):
        guard = DiversificationGuard(_config(
            max_product=0.10, max_marketplace=0.10, max_category=0.10, max_positions=1,
        ))
        existing = _position(opp_id=1, amount=800, product_id=42,
                             marketplace="amazon", category="electronics")
        new_pos = _position(opp_id=2, amount=800, product_id=42,
                            marketplace="amazon", category="electronics")
        portfolio = _portfolio(total=10000, positions=[existing])

        result = guard.check(new_pos, portfolio)
        assert result.is_safe is False
        # Should report: max_positions, product, marketplace, category
        assert len(result.violations) >= 3


class TestEmptyPortfolio:

    def test_first_position_always_passes(self):
        guard = DiversificationGuard(_config())
        portfolio = _portfolio(total=10000, positions=[])
        result = guard.check(_position(), portfolio)
        assert result.is_safe is True
