"""
Tests — Time-to-Sell Model: fast vs slow products, time decay.
"""
import pytest

from truth_engine.config import TruthEngineConfig, TimeModelWeights
from truth_engine.time_model import TimeModel, TimeEstimate


class TestTimeModel:

    def test_fast_product(self):
        """High velocity + high reviews + competitive price → fast sale."""
        model = TimeModel()
        result = model.estimate_time_to_sell(
            estimated_monthly_sales=25.0,
            reviews_count=150,
            competitor_count=5,
            price_vs_lowest_pct=0.98,
            profit=50.0,
        )
        assert result.expected_days < 4.0
        assert result.time_decay > 0.20
        assert result.adjusted_profit > 0
        assert result.velocity_signal > 0.7
        assert result.competitiveness_signal == 1.0

    def test_slow_product(self):
        """Low velocity + few reviews + overpriced → slow sale."""
        model = TimeModel()
        result = model.estimate_time_to_sell(
            estimated_monthly_sales=1.0,
            reviews_count=5,
            competitor_count=20,
            price_vs_lowest_pct=1.15,
            profit=50.0,
        )
        assert result.expected_days > 8.0
        assert result.time_decay < 0.15
        assert result.adjusted_profit < result.time_decay * 50 + 1

    def test_time_decay_formula(self):
        """Verify time_decay = 1 / (1 + expected_days)."""
        model = TimeModel()
        result = model.estimate_time_to_sell(
            estimated_monthly_sales=15.0,
            reviews_count=100,
            competitor_count=5,
            price_vs_lowest_pct=1.0,
            profit=100.0,
        )
        expected_decay = 1.0 / (1.0 + result.expected_days)
        assert result.time_decay == pytest.approx(expected_decay, abs=0.001)

    def test_adjusted_profit_equals_profit_times_decay(self):
        model = TimeModel()
        profit = 80.0
        result = model.estimate_time_to_sell(
            estimated_monthly_sales=10.0,
            reviews_count=50,
            competitor_count=8,
            price_vs_lowest_pct=1.05,
            profit=profit,
        )
        assert result.adjusted_profit == pytest.approx(
            profit * result.time_decay, abs=0.02,
        )

    def test_at_or_below_lowest_price_is_competitive(self):
        model = TimeModel()
        result = model.estimate_time_to_sell(
            estimated_monthly_sales=10.0,
            reviews_count=50,
            competitor_count=5,
            price_vs_lowest_pct=0.95,  # Below lowest
            profit=50.0,
        )
        assert result.competitiveness_signal == 1.0

    def test_far_above_lowest_is_uncompetitive(self):
        model = TimeModel()
        result = model.estimate_time_to_sell(
            estimated_monthly_sales=10.0,
            reviews_count=50,
            competitor_count=5,
            price_vs_lowest_pct=1.25,  # 25% above lowest
            profit=50.0,
        )
        assert result.competitiveness_signal == 0.0

    def test_time_decay_disabled(self):
        cfg = TruthEngineConfig(time_decay_enabled=False)
        model = TimeModel(config=cfg)
        result = model.estimate_time_to_sell(
            estimated_monthly_sales=1.0,
            reviews_count=5,
            competitor_count=20,
            price_vs_lowest_pct=1.15,
            profit=50.0,
        )
        assert result.time_decay == 1.0
        assert result.adjusted_profit == 50.0

    def test_zero_sales_uses_slow_defaults(self):
        model = TimeModel()
        result = model.estimate_time_to_sell(
            estimated_monthly_sales=0.0,
            reviews_count=0,
            competitor_count=0,
            price_vs_lowest_pct=1.0,
            profit=50.0,
        )
        assert result.expected_days >= model._cfg.fast_sale_days
        assert result.velocity_signal == 0.0
        assert result.reviews_signal == 0.0
