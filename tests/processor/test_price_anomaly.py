"""
Unit Tests — Price Anomaly Detection.

Tests:
  - Absolute bounds per currency
  - Median-based deviation detection
  - Minimum history size requirement
  - Edge cases (zero median, empty history)
"""
import pytest

from processor.price_anomaly import detect_price_anomaly, CURRENCY_BOUNDS, MIN_HISTORY_SIZE


class TestAbsoluteBounds:

    def test_usd_within_bounds(self):
        assert detect_price_anomaly(29.99, [], "USD") is False

    def test_usd_below_minimum(self):
        assert detect_price_anomaly(0.10, [], "USD") is True

    def test_usd_above_maximum(self):
        assert detect_price_anomaly(99999.0, [], "USD") is True

    def test_ars_within_bounds(self):
        assert detect_price_anomaly(100000.0, [], "ARS") is False

    def test_ars_below_minimum(self):
        assert detect_price_anomaly(100.0, [], "ARS") is True

    def test_mxn_within_bounds(self):
        assert detect_price_anomaly(5000.0, [], "MXN") is False

    def test_mxn_below_minimum(self):
        assert detect_price_anomaly(5.0, [], "MXN") is True

    def test_eur_within_bounds(self):
        assert detect_price_anomaly(89.99, [], "EUR") is False

    def test_zero_price_rejected(self):
        assert detect_price_anomaly(0.0, [], "USD") is True

    def test_unknown_currency_uses_default(self):
        # Default bounds: (1.0, 500_000)
        assert detect_price_anomaly(0.5, [], "XYZ") is True
        assert detect_price_anomaly(50.0, [], "XYZ") is False


class TestMedianDeviation:

    def test_normal_price_accepted(self):
        history = [100.0, 105.0, 110.0, 95.0, 102.0]
        assert detect_price_anomaly(108.0, history, "USD") is False

    def test_5x_above_median_rejected(self):
        history = [100.0, 105.0, 110.0, 95.0, 102.0]
        # Median ≈ 102, 5x = 510
        assert detect_price_anomaly(600.0, history, "USD") is True

    def test_02x_below_median_rejected(self):
        history = [100.0, 105.0, 110.0, 95.0, 102.0]
        # Median ≈ 102, 0.2x = 20.4
        assert detect_price_anomaly(15.0, history, "USD") is True

    def test_borderline_above_accepted(self):
        history = [100.0, 105.0, 110.0, 95.0, 102.0]
        # Median ≈ 102, 5x = 510, price = 500 (just below)
        assert detect_price_anomaly(500.0, history, "USD") is False

    def test_borderline_below_accepted(self):
        history = [100.0, 105.0, 110.0, 95.0, 102.0]
        # Median ≈ 102, 0.2x = 20.4, price = 25 (just above)
        assert detect_price_anomaly(25.0, history, "USD") is False


class TestHistoryRequirements:

    def test_too_few_points_skips_median(self):
        """With < MIN_HISTORY_SIZE points, only absolute bounds apply."""
        history = [100.0, 105.0]  # Only 2 points
        # Price is 5x above, but history too small to judge
        assert detect_price_anomaly(600.0, history, "USD") is False

    def test_exactly_min_history_applies_median(self):
        history = [100.0, 105.0, 110.0]  # Exactly MIN_HISTORY_SIZE
        # Median = 105, 5x = 525, price = 600 > 525
        assert detect_price_anomaly(600.0, history, "USD") is True

    def test_empty_history_only_bounds(self):
        assert detect_price_anomaly(100.0, [], "USD") is False
        assert detect_price_anomaly(0.10, [], "USD") is True

    def test_zero_median_accepted(self):
        """If all historical prices are 0, median is 0 — skip ratio check."""
        history = [0.0, 0.0, 0.0, 0.0]
        # median = 0, skip ratio check, pass absolute bounds
        assert detect_price_anomaly(50.0, history, "USD") is False
