"""Tests for the prediction engine domain layer."""
import pytest
from datetime import datetime, timezone, timedelta

from engines.prediction.domain.enums import ForecastHorizon, ModelType, PredictionStatus
from engines.prediction.domain.models import PredictionRequest, PredictionResult, PriceForecast, PricePoint
from engines.prediction.domain.strategies import BaselineStrategy, XGBoostStrategy, ProphetStrategy
from engines.prediction.application.model_registry import ModelRegistry, create_default_registry


# ─── PricePoint & domain model tests ────────────────────────────────

class TestDomainModels:
    def test_price_point_creation(self):
        pp = PricePoint(price=99.99, recorded_at=datetime.now(timezone.utc))
        assert pp.price == 99.99

    def test_prediction_request_defaults(self):
        req = PredictionRequest(product_id=1)
        assert req.horizon == ForecastHorizon.DAYS_7
        assert req.model_type is None

    def test_prediction_result_validation(self):
        result = PredictionResult(
            product_id=1,
            model_type=ModelType.BASELINE,
            horizon_days=7,
            predicted_price=100.0,
            confidence=0.8,
        )
        assert result.status == PredictionStatus.COMPUTED
        assert 0 <= result.confidence <= 1

    def test_price_forecast_from_attributes(self):
        forecast = PriceForecast(
            product_id=1,
            model_type="baseline",
            horizon_days=7,
            predicted_price=50.0,
        )
        assert forecast.features_used == {}


# ─── BaselineStrategy tests ─────────────────────────────────────────

class TestBaselineStrategy:
    def setup_method(self):
        self.strategy = BaselineStrategy()
        self.base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

    def _make_history(self, prices: list[float]) -> list[PricePoint]:
        return [
            PricePoint(
                price=p,
                recorded_at=self.base_time + timedelta(days=i),
            )
            for i, p in enumerate(prices)
        ]

    def test_model_type(self):
        assert self.strategy.model_type == ModelType.BASELINE

    def test_min_data_points(self):
        assert self.strategy.min_data_points == 3

    def test_insufficient_data(self):
        history = self._make_history([100.0, 110.0])
        result = self.strategy.predict(1, history, ForecastHorizon.DAYS_7)
        assert result.status == PredictionStatus.FAILED
        assert result.confidence == 0

    def test_stable_prices(self):
        # Flat prices should predict roughly the same price
        history = self._make_history([100.0] * 10)
        result = self.strategy.predict(1, history, ForecastHorizon.DAYS_7)
        assert result.status == PredictionStatus.COMPUTED
        assert abs(result.predicted_price - 100.0) < 5.0
        assert result.confidence > 0.5

    def test_rising_trend(self):
        # Linearly increasing prices
        prices = [100 + i * 2 for i in range(20)]
        history = self._make_history(prices)
        result = self.strategy.predict(1, history, ForecastHorizon.DAYS_7)
        assert result.predicted_price > prices[-1]
        assert result.features_used["slope_per_day"] > 0

    def test_falling_trend(self):
        prices = [200 - i * 3 for i in range(20)]
        history = self._make_history(prices)
        result = self.strategy.predict(1, history, ForecastHorizon.DAYS_7)
        assert result.predicted_price < prices[-1]

    def test_confidence_interval(self):
        history = self._make_history([100, 105, 98, 103, 101, 99, 104, 102])
        result = self.strategy.predict(1, history, ForecastHorizon.DAYS_7)
        assert result.confidence_lower is not None
        assert result.confidence_upper is not None
        assert result.confidence_lower < result.predicted_price
        assert result.confidence_upper > result.predicted_price

    def test_longer_horizon_wider_interval(self):
        history = self._make_history([100, 105, 98, 103, 101, 99, 104, 102])
        result_7 = self.strategy.predict(1, history, ForecastHorizon.DAYS_7)
        result_30 = self.strategy.predict(1, history, ForecastHorizon.DAYS_30)
        ci_7 = result_7.confidence_upper - result_7.confidence_lower
        ci_30 = result_30.confidence_upper - result_30.confidence_lower
        assert ci_30 > ci_7

    def test_predicted_price_never_negative(self):
        # Steeply falling prices
        prices = [10, 8, 5, 3, 1]
        history = self._make_history(prices)
        result = self.strategy.predict(1, history, ForecastHorizon.DAYS_30)
        assert result.predicted_price > 0


# ─── ModelRegistry tests ────────────────────────────────────────────

class TestModelRegistry:
    def test_register_and_get(self):
        registry = ModelRegistry()
        strategy = BaselineStrategy()
        registry.register(strategy)
        assert registry.get(ModelType.BASELINE) is strategy

    def test_get_missing_raises(self):
        registry = ModelRegistry()
        with pytest.raises(KeyError):
            registry.get(ModelType.PROPHET)

    def test_get_or_fallback(self):
        registry = ModelRegistry()
        registry.register(BaselineStrategy())
        result = registry.get_or_fallback(ModelType.PROPHET)
        assert result.model_type == ModelType.BASELINE

    def test_available(self):
        registry = create_default_registry()
        available = registry.available()
        assert ModelType.BASELINE in available
        assert ModelType.XGBOOST in available
        assert ModelType.PROPHET in available

    def test_select_best_few_data_points(self):
        registry = create_default_registry()
        strategy = registry.select_best(5)
        assert strategy.model_type == ModelType.BASELINE

    def test_select_best_medium_data_points(self):
        registry = create_default_registry()
        strategy = registry.select_best(20)
        # Should prefer XGBoost for 14+ data points
        assert strategy.model_type == ModelType.XGBOOST

    def test_select_best_many_data_points(self):
        registry = create_default_registry()
        strategy = registry.select_best(60)
        assert strategy.model_type == ModelType.PROPHET


# ─── XGBoost strategy (falls back to baseline without xgboost) ──────

class TestXGBoostStrategy:
    def test_model_type(self):
        s = XGBoostStrategy()
        assert s.model_type == ModelType.XGBOOST

    def test_min_data_points(self):
        assert XGBoostStrategy().min_data_points == 14

    def test_fallback_on_insufficient_data(self):
        s = XGBoostStrategy()
        base = datetime(2025, 1, 1, tzinfo=timezone.utc)
        history = [PricePoint(price=100, recorded_at=base + timedelta(days=i)) for i in range(5)]
        result = s.predict(1, history, ForecastHorizon.DAYS_7)
        # Should fallback to baseline
        assert result.model_type in (ModelType.BASELINE, ModelType.XGBOOST)
        assert result.status == PredictionStatus.COMPUTED


# ─── Prophet strategy (falls back to baseline without prophet) ──────

class TestProphetStrategy:
    def test_model_type(self):
        s = ProphetStrategy()
        assert s.model_type == ModelType.PROPHET

    def test_min_data_points(self):
        assert ProphetStrategy().min_data_points == 10
