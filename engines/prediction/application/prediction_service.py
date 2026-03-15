"""Prediction service — orchestrates strategy selection, execution, and persistence."""
import logging
from typing import Optional

from ..domain.enums import ForecastHorizon, ModelType, PredictionStatus
from ..domain.models import PredictionRequest, PredictionResult, PriceForecast, PricePoint
from ..infrastructure.prediction_repository import PredictionRepository
from ..infrastructure.redis_publisher import PredictionEventPublisher
from .model_registry import ModelRegistry

logger = logging.getLogger(__name__)


class PredictionService:
    """Application service for price predictions.

    Coordinates between domain strategies, persistence, and events.
    """

    def __init__(
        self,
        repository: PredictionRepository,
        registry: ModelRegistry,
        publisher: Optional[PredictionEventPublisher] = None,
    ) -> None:
        self._repo = repository
        self._registry = registry
        self._publisher = publisher

    async def predict(self, request: PredictionRequest) -> PriceForecast:
        """Generate a price prediction for a product."""
        # Fetch price history
        price_history = await self._repo.get_price_history(
            request.product_id, days=90
        )

        if not price_history:
            logger.warning("No price history for product %d", request.product_id)
            return PriceForecast(
                product_id=request.product_id,
                model_type="baseline",
                horizon_days=request.horizon.value,
                predicted_price=0,
                confidence=0,
                status="failed",
            )

        # Select strategy
        if request.model_type:
            strategy = self._registry.get_or_fallback(request.model_type)
        else:
            strategy = self._registry.select_best(len(price_history))

        logger.info(
            "Predicting product=%d horizon=%dd strategy=%s data_points=%d",
            request.product_id, request.horizon.value,
            strategy.model_type.value, len(price_history),
        )

        # Run prediction
        result = strategy.predict(request.product_id, price_history, request.horizon)

        # Persist
        forecast = PriceForecast(
            product_id=result.product_id,
            model_type=result.model_type.value,
            horizon_days=result.horizon_days,
            predicted_price=result.predicted_price,
            confidence_lower=result.confidence_lower,
            confidence_upper=result.confidence_upper,
            mape=result.mape,
            confidence=result.confidence,
            features_used=result.features_used,
            status=result.status.value,
        )
        await self._repo.save_prediction(forecast)

        # Publish event
        if self._publisher and result.status == PredictionStatus.COMPUTED:
            await self._publisher.publish("prediction_generated", {
                "product_id": result.product_id,
                "model_type": result.model_type.value,
                "horizon_days": result.horizon_days,
                "predicted_price": result.predicted_price,
                "confidence": result.confidence,
            })

        return forecast

    async def predict_batch(
        self,
        product_ids: list[int],
        horizon: ForecastHorizon = ForecastHorizon.DAYS_7,
        model_type: Optional[ModelType] = None,
    ) -> list[PriceForecast]:
        """Generate predictions for multiple products."""
        results = []
        for pid in product_ids:
            try:
                request = PredictionRequest(
                    product_id=pid, horizon=horizon, model_type=model_type
                )
                forecast = await self.predict(request)
                results.append(forecast)
            except Exception:
                logger.error("Prediction failed for product %d", pid, exc_info=True)
        return results

    async def get_predictions(
        self, product_id: int, horizon: Optional[int] = None
    ) -> list[PriceForecast]:
        """Retrieve stored predictions for a product."""
        return await self._repo.get_predictions(product_id, horizon)

    async def get_top_product_ids(self, limit: int = 50) -> list[int]:
        """Get product IDs most suitable for prediction (have price history)."""
        return await self._repo.get_products_with_history(limit)
