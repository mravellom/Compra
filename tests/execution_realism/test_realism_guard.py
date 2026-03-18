"""
Tests — Realism Guard: full integration of all execution realism checks.
"""
import pytest
from datetime import datetime, timedelta, timezone

from execution_realism.config import (
    CompetitionConfig,
    ExecutionRealismConfig,
    LatencyConfig,
    PriceValidationConfig,
    SlippageConfig,
    StockValidationConfig,
)
from execution_realism.realism_guard import RealismGuard


def _cfg(**overrides) -> ExecutionRealismConfig:
    return ExecutionRealismConfig(
        price=overrides.get("price", PriceValidationConfig()),
        stock=overrides.get("stock", StockValidationConfig()),
        latency=overrides.get("latency", LatencyConfig()),
        slippage=overrides.get("slippage", SlippageConfig()),
        competition=overrides.get("competition", CompetitionConfig()),
        min_execution_confidence=overrides.get("min_conf", 0.30),
    )


def _good_inputs() -> dict:
    """Default inputs that should pass all checks."""
    return dict(
        expected_buy_price=100.0,
        current_buy_price=101.0,         # 1% deviation — within 5%
        expected_sell_price=200.0,
        total_fees=30.0,
        stock_available=10,
        seller_stockout_count=0,
        detected_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        price_volatility=0.05,
        estimated_monthly_sales=8.0,
        roi=0.20,
        reviews_count=80,
        competitor_count=8,
        probability_of_sale=0.65,
    )


class TestFullApproval:

    def test_good_opportunity_approved(self):
        guard = RealismGuard(_cfg())
        result = guard.evaluate(**_good_inputs())

        assert result.approved is True
        assert len(result.rejection_reasons) == 0
        assert result.price_check is not None
        assert result.stock_check is not None
        assert result.slippage is not None
        assert result.latency is not None
        assert result.competition is not None
        assert result.execution_confidence is not None
        assert result.final_buy_price > 0
        assert result.final_profit > 0
        assert result.final_p_sale > 0

    def test_returns_all_sub_results(self):
        guard = RealismGuard(_cfg())
        result = guard.evaluate(**_good_inputs())

        assert result.price_check.is_valid is True
        assert result.stock_check.available is True
        assert result.slippage.is_viable is True
        assert result.latency.is_acceptable is True
        assert result.execution_confidence.is_acceptable is True


class TestPriceRejection:

    def test_large_deviation_rejects(self):
        guard = RealismGuard(_cfg(price=PriceValidationConfig(max_price_deviation_pct=0.03)))
        inputs = _good_inputs()
        inputs["current_buy_price"] = 110.0  # 10% deviation
        result = guard.evaluate(**inputs)

        assert result.approved is False
        assert any("price" in r for r in result.rejection_reasons)


class TestStockRejection:

    def test_out_of_stock_rejects(self):
        guard = RealismGuard(_cfg())
        inputs = _good_inputs()
        inputs["stock_available"] = 0
        result = guard.evaluate(**inputs)

        assert result.approved is False
        assert any("stock" in r for r in result.rejection_reasons)


class TestSlippageRejection:

    def test_slippage_kills_thin_margin(self):
        guard = RealismGuard(_cfg(slippage=SlippageConfig(
            min_profit_after_slippage_usd=20.0,
            base_slippage_pct=0.05,
        )))
        inputs = _good_inputs()
        inputs["expected_buy_price"] = 180.0
        inputs["current_buy_price"] = 180.0
        inputs["expected_sell_price"] = 210.0
        inputs["total_fees"] = 5.0
        inputs["price_volatility"] = 0.2
        # Profit before slippage: 210-180-5=25, after ~5-8% slippage: <20
        result = guard.evaluate(**inputs)

        assert result.approved is False
        assert any("slippage" in r for r in result.rejection_reasons)


class TestLatencyRejection:

    def test_stale_opportunity_rejects(self):
        guard = RealismGuard(_cfg(latency=LatencyConfig(max_latency_seconds=120)))
        inputs = _good_inputs()
        inputs["detected_at"] = datetime.now(timezone.utc) - timedelta(minutes=10)
        result = guard.evaluate(**inputs)

        assert result.approved is False
        assert any("latency" in r for r in result.rejection_reasons)


class TestConfidenceRejection:

    def test_low_confidence_rejects(self):
        guard = RealismGuard(_cfg(min_conf=0.99))  # Impossible threshold
        result = guard.evaluate(**_good_inputs())

        assert result.approved is False
        assert any("confidence" in r for r in result.rejection_reasons)


class TestCompetitionEffect:

    def test_high_competition_reduces_p_sale(self):
        guard = RealismGuard(_cfg())
        inputs = _good_inputs()
        inputs["roi"] = 0.60
        inputs["reviews_count"] = 500
        inputs["estimated_monthly_sales"] = 25
        inputs["competitor_count"] = 25

        result = guard.evaluate(**inputs)
        assert result.competition.competition_score > 0.2
        assert result.final_p_sale < inputs["probability_of_sale"]


class TestMultipleRejections:

    def test_collects_all_reasons(self):
        guard = RealismGuard(_cfg(
            price=PriceValidationConfig(max_price_deviation_pct=0.01),
            latency=LatencyConfig(max_latency_seconds=10),
        ))
        inputs = _good_inputs()
        inputs["current_buy_price"] = 110.0  # 10% off
        inputs["detected_at"] = datetime.now(timezone.utc) - timedelta(minutes=5)
        result = guard.evaluate(**inputs)

        assert result.approved is False
        assert len(result.rejection_reasons) >= 2


class TestFinalValues:

    def test_final_buy_includes_slippage(self):
        guard = RealismGuard(_cfg())
        inputs = _good_inputs()
        result = guard.evaluate(**inputs)

        if result.approved:
            # final_buy should be slipped price (higher than current)
            assert result.final_buy_price >= inputs["current_buy_price"]

    def test_final_profit_less_than_original(self):
        guard = RealismGuard(_cfg())
        inputs = _good_inputs()
        result = guard.evaluate(**inputs)

        original_profit = (
            inputs["expected_sell_price"]
            - inputs["expected_buy_price"]
            - inputs["total_fees"]
        )
        if result.approved:
            assert result.final_profit <= original_profit
