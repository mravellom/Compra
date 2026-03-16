"""Price prediction strategies using the Strategy Pattern.

Each strategy implements predict() taking a price history series
and returning a PredictionResult. Strategies are stateless and
can be swapped at runtime via ModelRegistry.
"""
import logging
import math
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from .enums import ForecastHorizon, ModelType, PredictionStatus
from .models import PredictionResult, PricePoint

logger = logging.getLogger(__name__)


class PredictionStrategy(ABC):
    """Abstract base for all prediction models."""

    @property
    @abstractmethod
    def model_type(self) -> ModelType:
        ...

    @abstractmethod
    def predict(
        self,
        product_id: int,
        price_history: list[PricePoint],
        horizon: ForecastHorizon,
    ) -> PredictionResult:
        ...

    @property
    def min_data_points(self) -> int:
        """Minimum history length required."""
        return 3


class BaselineStrategy(PredictionStrategy):
    """Simple moving average + linear trend extrapolation.

    Works with minimal data. Always available as a fallback.
    """

    @property
    def model_type(self) -> ModelType:
        return ModelType.BASELINE

    def predict(
        self,
        product_id: int,
        price_history: list[PricePoint],
        horizon: ForecastHorizon,
    ) -> PredictionResult:
        if len(price_history) < self.min_data_points:
            return self._insufficient_data(product_id, horizon)

        prices = np.array([p.price for p in price_history])
        timestamps = np.array([p.recorded_at.timestamp() for p in price_history])

        # Linear regression: price = slope * time + intercept
        n = len(prices)
        mean_t = np.mean(timestamps)
        mean_p = np.mean(prices)
        slope = (
            np.sum((timestamps - mean_t) * (prices - mean_p))
            / (np.sum((timestamps - mean_t) ** 2) + 1e-10)
        )
        intercept = mean_p - slope * mean_t

        # Predict at horizon
        last_ts = timestamps[-1]
        future_ts = last_ts + (horizon.value * 86400)
        predicted = slope * future_ts + intercept

        # Don't predict negative prices
        predicted = max(predicted, 0.01)

        # Confidence from residuals
        fitted = slope * timestamps + intercept
        residuals = prices - fitted
        std_residual = float(np.std(residuals)) if n > 2 else float(np.std(prices))

        # Wider confidence for longer horizons
        horizon_factor = math.sqrt(horizon.value / 7.0)
        ci_width = 1.96 * std_residual * horizon_factor

        # MAPE on training data
        mape = float(np.mean(np.abs(residuals / (prices + 1e-10)))) * 100

        # Confidence score: lower MAPE and more data = higher confidence
        data_factor = min(n / 30.0, 1.0)
        mape_factor = max(0, 1.0 - mape / 50.0)
        confidence = 0.3 * data_factor + 0.7 * mape_factor
        confidence = max(0.1, min(0.9, confidence))

        return PredictionResult(
            product_id=product_id,
            model_type=ModelType.BASELINE,
            horizon_days=horizon.value,
            predicted_price=round(predicted, 2),
            confidence_lower=round(max(predicted - ci_width, 0.01), 2),
            confidence_upper=round(predicted + ci_width, 2),
            mape=round(mape, 2),
            confidence=round(confidence, 3),
            features_used={"method": "linear_regression", "data_points": n, "slope_per_day": round(slope * 86400, 4)},
        )

    def _insufficient_data(self, product_id: int, horizon: ForecastHorizon) -> PredictionResult:
        return PredictionResult(
            product_id=product_id,
            model_type=ModelType.BASELINE,
            horizon_days=horizon.value,
            predicted_price=0,
            confidence=0,
            mape=None,
            status=PredictionStatus.FAILED,
            features_used={"error": "insufficient_data"},
        )


class XGBoostStrategy(PredictionStrategy):
    """XGBoost-based prediction with engineered features.

    Features: lag prices, rolling means, day-of-week, price volatility.
    Falls back to Baseline if xgboost is unavailable.
    """

    @property
    def model_type(self) -> ModelType:
        return ModelType.XGBOOST

    @property
    def min_data_points(self) -> int:
        return 14  # Need enough for lag features

    def predict(
        self,
        product_id: int,
        price_history: list[PricePoint],
        horizon: ForecastHorizon,
    ) -> PredictionResult:
        try:
            import xgboost as xgb
        except ImportError:
            logger.warning("xgboost not installed, falling back to baseline for product %d", product_id)
            return BaselineStrategy().predict(product_id, price_history, horizon)

        if len(price_history) < self.min_data_points:
            return BaselineStrategy().predict(product_id, price_history, horizon)

        # Sort by time
        sorted_history = sorted(price_history, key=lambda p: p.recorded_at)
        prices = np.array([p.price for p in sorted_history])
        dates = [p.recorded_at for p in sorted_history]

        # Build features
        features, targets = self._build_features(prices, dates)
        if len(features) < 5:
            return BaselineStrategy().predict(product_id, price_history, horizon)

        X = np.array(features)
        y = np.array(targets)

        # Train/val split (last 20% for validation)
        split_idx = max(int(len(X) * 0.8), 1)
        X_train, X_val = X[:split_idx], X[split_idx:]
        y_train, y_val = y[:split_idx], y[split_idx:]

        dtrain = xgb.DMatrix(X_train, label=y_train)
        dval = xgb.DMatrix(X_val, label=y_val) if len(X_val) > 0 else dtrain

        params = {
            "objective": "reg:squarederror",
            "max_depth": 4,
            "learning_rate": 0.1,
            "subsample": 0.8,
            "min_child_weight": 3,
            "verbosity": 0,
        }

        model = xgb.train(
            params, dtrain, num_boost_round=100,
            evals=[(dval, "val")],
            early_stopping_rounds=10,
            verbose_eval=False,
        )

        # Generate future features for prediction
        last_prices = prices[-7:]
        future_features = self._build_future_features(
            prices, dates, horizon.value
        )
        dpred = xgb.DMatrix(np.array([future_features]))
        predicted = float(model.predict(dpred)[0])
        predicted = max(predicted, 0.01)

        # Validation MAPE
        mape = 0.0
        if len(X_val) > 0:
            val_pred = model.predict(dval)
            mape = float(np.mean(np.abs((y_val - val_pred) / (y_val + 1e-10)))) * 100

        # Confidence interval from validation residuals
        std_err = float(np.std(prices[-7:])) if len(prices) >= 7 else float(np.std(prices))
        horizon_factor = math.sqrt(horizon.value / 7.0)
        ci_width = 1.96 * std_err * horizon_factor

        data_factor = min(len(prices) / 60.0, 1.0)
        mape_factor = max(0, 1.0 - mape / 30.0)
        confidence = 0.4 * data_factor + 0.6 * mape_factor
        confidence = max(0.1, min(0.95, confidence))

        return PredictionResult(
            product_id=product_id,
            model_type=ModelType.XGBOOST,
            horizon_days=horizon.value,
            predicted_price=round(predicted, 2),
            confidence_lower=round(max(predicted - ci_width, 0.01), 2),
            confidence_upper=round(predicted + ci_width, 2),
            mape=round(mape, 2),
            confidence=round(confidence, 3),
            features_used={
                "method": "xgboost",
                "data_points": len(prices),
                "features": ["lag_1-7", "rolling_mean_7", "rolling_std_7", "day_of_week", "trend"],
                "best_iteration": model.best_iteration if hasattr(model, "best_iteration") else None,
            },
        )

    def _build_features(
        self, prices: np.ndarray, dates: list[datetime]
    ) -> tuple[list[list[float]], list[float]]:
        """Build supervised learning features from price series."""
        features = []
        targets = []
        window = 7

        for i in range(window, len(prices)):
            row = []
            # Lag features (1-7)
            for lag in range(1, window + 1):
                row.append(prices[i - lag])
            # Rolling mean/std
            window_prices = prices[i - window:i]
            row.append(float(np.mean(window_prices)))
            row.append(float(np.std(window_prices)))
            # Day of week
            row.append(float(dates[i].weekday()))
            # Linear trend (slope over window)
            x = np.arange(window, dtype=float)
            slope = float(np.polyfit(x, window_prices, 1)[0])
            row.append(slope)

            features.append(row)
            targets.append(prices[i])

        return features, targets

    def _build_future_features(
        self, prices: np.ndarray, dates: list[datetime], horizon_days: int
    ) -> list[float]:
        """Build feature vector for future prediction."""
        window = 7
        last_window = prices[-window:]
        row: list[float] = []
        # Lag features
        for lag in range(1, window + 1):
            idx = -lag
            row.append(float(prices[idx]) if abs(idx) <= len(prices) else float(prices[0]))
        # Rolling stats
        row.append(float(np.mean(last_window)))
        row.append(float(np.std(last_window)))
        # Projected day of week
        future_date = dates[-1] + timedelta(days=horizon_days)
        row.append(float(future_date.weekday()))
        # Trend
        x = np.arange(window, dtype=float)
        slope = float(np.polyfit(x, last_window, 1)[0])
        row.append(slope)
        return row


class ProphetStrategy(PredictionStrategy):
    """Facebook Prophet time-series forecasting.

    Handles seasonality and trend changepoints automatically.
    Falls back to Baseline if prophet is unavailable.
    """

    # Lazy model cache: product_id -> (model, fitted_at_timestamp)
    _model_cache: dict[int, tuple] = {}
    _CACHE_TTL = 3600  # 1 hour
    _MAX_CACHE_SIZE = 100

    @property
    def model_type(self) -> ModelType:
        return ModelType.PROPHET

    @property
    def min_data_points(self) -> int:
        return 10

    def predict(
        self,
        product_id: int,
        price_history: list[PricePoint],
        horizon: ForecastHorizon,
    ) -> PredictionResult:
        try:
            from prophet import Prophet  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("prophet not installed, falling back to baseline for product %d", product_id)
            return BaselineStrategy().predict(product_id, price_history, horizon)

        if len(price_history) < self.min_data_points:
            return BaselineStrategy().predict(product_id, price_history, horizon)

        import pandas as pd

        sorted_history = sorted(price_history, key=lambda p: p.recorded_at)

        df = pd.DataFrame({
            "ds": [p.recorded_at.replace(tzinfo=None) for p in sorted_history],
            "y": [p.price for p in sorted_history],
        })

        # Suppress Prophet logging
        import logging as _logging
        _logging.getLogger("prophet").setLevel(_logging.WARNING)
        _logging.getLogger("cmdstanpy").setLevel(_logging.WARNING)

        # Try cached model first
        cached = self._model_cache.get(product_id)
        if cached is not None and (time.time() - cached[1] < self._CACHE_TTL):
            model = cached[0]
        else:
            model = Prophet(
                yearly_seasonality=False,
                weekly_seasonality=True if len(df) >= 14 else False,
                daily_seasonality=False,
                changepoint_prior_scale=0.05,
            )
            model.fit(df)

            # Cache fitted model
            if len(self._model_cache) >= self._MAX_CACHE_SIZE:
                oldest_key = min(self._model_cache, key=lambda k: self._model_cache[k][1])
                del self._model_cache[oldest_key]
            self._model_cache[product_id] = (model, time.time())

        future = model.make_future_dataframe(periods=horizon.value)
        forecast = model.predict(future)

        # Get prediction at horizon
        last_row = forecast.iloc[-1]
        predicted = max(float(last_row["yhat"]), 0.01)
        lower = max(float(last_row["yhat_lower"]), 0.01)
        upper = float(last_row["yhat_upper"])

        # MAPE on fitted values
        fitted = forecast.iloc[:len(df)]["yhat"].values
        actual = df["y"].values
        mape = float(np.mean(np.abs((actual - fitted) / (actual + 1e-10)))) * 100

        data_factor = min(len(df) / 60.0, 1.0)
        mape_factor = max(0, 1.0 - mape / 25.0)
        confidence = 0.35 * data_factor + 0.65 * mape_factor
        confidence = max(0.1, min(0.95, confidence))

        return PredictionResult(
            product_id=product_id,
            model_type=ModelType.PROPHET,
            horizon_days=horizon.value,
            predicted_price=round(predicted, 2),
            confidence_lower=round(lower, 2),
            confidence_upper=round(upper, 2),
            mape=round(mape, 2),
            confidence=round(confidence, 3),
            features_used={
                "method": "prophet",
                "data_points": len(df),
                "changepoints": len(model.changepoints) if hasattr(model, "changepoints") else 0,
            },
        )
