"""
Tests — Stock Validator: availability, low stock penalty, stockout history.
"""
import pytest

from execution_realism.config import StockValidationConfig
from execution_realism.stock_validator import StockValidator


class TestStockAvailability:

    def test_in_stock_passes(self):
        v = StockValidator()
        result = v.check(stock_available=10)
        assert result.available is True
        assert result.confidence_adjustment == 1.0

    def test_out_of_stock_rejects(self):
        v = StockValidator()
        result = v.check(stock_available=0)
        assert result.available is False
        assert result.confidence_adjustment == 0.0

    def test_unknown_stock_cautious_pass(self):
        v = StockValidator()
        result = v.check(stock_available=None)
        assert result.available is True
        assert result.confidence_adjustment < 1.0


class TestLowStock:

    def test_low_stock_reduces_confidence(self):
        v = StockValidator(StockValidationConfig(
            low_stock_threshold=3,
            low_stock_confidence_penalty=0.15,
        ))
        result = v.check(stock_available=2)
        assert result.available is True
        assert result.confidence_adjustment == pytest.approx(0.85, abs=0.01)

    def test_above_threshold_no_penalty(self):
        v = StockValidator(StockValidationConfig(low_stock_threshold=3))
        result = v.check(stock_available=5)
        assert result.confidence_adjustment == 1.0


class TestStockoutHistory:

    def test_frequent_stockout_penalized(self):
        v = StockValidator(StockValidationConfig(
            frequent_stockout_threshold=3,
            frequent_stockout_penalty=0.20,
        ))
        result = v.check(stock_available=10, seller_stockout_count=5)
        assert result.available is True
        assert result.confidence_adjustment == pytest.approx(0.80, abs=0.01)

    def test_low_stockout_no_penalty(self):
        v = StockValidator(StockValidationConfig(frequent_stockout_threshold=3))
        result = v.check(stock_available=10, seller_stockout_count=1)
        assert result.confidence_adjustment == 1.0

    def test_unknown_stock_plus_stockout_compounds(self):
        v = StockValidator(StockValidationConfig(
            frequent_stockout_threshold=2,
            frequent_stockout_penalty=0.20,
        ))
        result = v.check(stock_available=None, seller_stockout_count=3)
        assert result.available is True
        # 0.90 * 0.80 = 0.72
        assert result.confidence_adjustment == pytest.approx(0.72, abs=0.01)
