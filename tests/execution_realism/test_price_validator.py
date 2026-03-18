"""
Tests — Price Validator: deviation checks, profit recomputation.
"""
import pytest

from execution_realism.config import PriceValidationConfig
from execution_realism.price_validator import PriceValidator


class TestPriceDeviation:

    def test_within_threshold_passes(self):
        v = PriceValidator(PriceValidationConfig(max_price_deviation_pct=0.05))
        result = v.validate(
            expected_buy_price=100.0, current_buy_price=103.0,
            expected_sell_price=200.0, total_fees=30.0,
        )
        assert result.is_valid is True
        assert result.price_delta_pct == pytest.approx(0.03, abs=0.001)

    def test_above_threshold_rejects(self):
        v = PriceValidator(PriceValidationConfig(max_price_deviation_pct=0.05))
        result = v.validate(
            expected_buy_price=100.0, current_buy_price=108.0,
            expected_sell_price=200.0, total_fees=30.0,
        )
        assert result.is_valid is False
        assert "deviation" in result.reason

    def test_price_drop_above_threshold_rejects(self):
        """Even a large price DROP is suspicious (data error, wrong product)."""
        v = PriceValidator(PriceValidationConfig(max_price_deviation_pct=0.05))
        result = v.validate(
            expected_buy_price=100.0, current_buy_price=90.0,
            expected_sell_price=200.0, total_fees=30.0,
        )
        assert result.is_valid is False

    def test_exact_price_passes(self):
        v = PriceValidator()
        result = v.validate(100.0, 100.0, 200.0, 30.0)
        assert result.is_valid is True
        assert result.price_delta_pct == 0.0


class TestProfitRecomputation:

    def test_updated_profit_computed(self):
        v = PriceValidator()
        result = v.validate(100.0, 102.0, 200.0, 30.0)
        # original: 200-100-30=70, updated: 200-102-30=68
        assert result.original_profit == 70.0
        assert result.updated_profit == 68.0

    def test_profit_below_min_rejects(self):
        v = PriceValidator(PriceValidationConfig(
            max_price_deviation_pct=0.10,
            min_profit_after_recheck_usd=20.0,
        ))
        result = v.validate(
            expected_buy_price=180.0, current_buy_price=185.0,
            expected_sell_price=200.0, total_fees=5.0,
        )
        # updated profit: 200-185-5=10 < 20
        assert result.is_valid is False
        assert "profit" in result.reason


class TestEdgeCases:

    def test_zero_expected_price_rejects(self):
        v = PriceValidator()
        result = v.validate(0.0, 50.0, 100.0, 10.0)
        assert result.is_valid is False

    def test_negative_expected_price_rejects(self):
        v = PriceValidator()
        result = v.validate(-10.0, 50.0, 100.0, 10.0)
        assert result.is_valid is False
