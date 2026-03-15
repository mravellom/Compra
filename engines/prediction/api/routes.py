"""Prediction engine API routes."""
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db, async_session
from ..application.model_registry import create_default_registry
from ..application.prediction_service import PredictionService
from ..domain.enums import ForecastHorizon, ModelType
from ..domain.models import PredictionRequest as DomainPredictionRequest
from ..infrastructure.prediction_repository import PredictionRepository
from ..infrastructure.redis_publisher import PredictionEventPublisher
from .schemas import BatchPredictionRequest, PredictionListOut, PredictionOut, PredictionRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/predictions", tags=["predictions"])


def _get_service() -> PredictionService:
    """Factory for PredictionService with default dependencies."""
    repo = PredictionRepository(async_session)
    registry = create_default_registry()
    # Redis publisher is optional; created if REDIS_URL is set
    publisher = None
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            import redis.asyncio as aioredis
            client = aioredis.from_url(redis_url, decode_responses=True)
            publisher = PredictionEventPublisher(client)
        except Exception:
            logger.warning("Redis unavailable for prediction events")
    return PredictionService(repo, registry, publisher)


@router.post("/", response_model=PredictionOut)
async def create_prediction(request: PredictionRequest):
    """Generate a price prediction for a product."""
    service = _get_service()

    horizon_map = {7: ForecastHorizon.DAYS_7, 14: ForecastHorizon.DAYS_14, 30: ForecastHorizon.DAYS_30}
    horizon = horizon_map.get(request.horizon_days)
    if horizon is None:
        raise HTTPException(400, f"Invalid horizon: {request.horizon_days}. Must be 7, 14, or 30.")

    model_type = None
    if request.model_type:
        try:
            model_type = ModelType(request.model_type)
        except ValueError:
            raise HTTPException(400, f"Invalid model_type: {request.model_type}")

    domain_req = DomainPredictionRequest(
        product_id=request.product_id,
        horizon=horizon,
        model_type=model_type,
    )

    forecast = await service.predict(domain_req)
    return PredictionOut(**forecast.model_dump())


@router.get("/{product_id}", response_model=PredictionListOut)
async def get_predictions(product_id: int, horizon_days: Optional[int] = None):
    """Get stored predictions for a product."""
    service = _get_service()
    predictions = await service.get_predictions(product_id, horizon_days)
    return PredictionListOut(
        predictions=[PredictionOut(**p.model_dump()) for p in predictions],
        total=len(predictions),
        product_id=product_id,
    )


@router.post("/batch", response_model=list[PredictionOut])
async def batch_predict(request: BatchPredictionRequest):
    """Generate predictions for multiple products."""
    service = _get_service()

    product_ids = request.product_ids
    if not product_ids:
        product_ids = await service.get_top_product_ids(request.limit)

    if not product_ids:
        return []

    horizon_map = {7: ForecastHorizon.DAYS_7, 14: ForecastHorizon.DAYS_14, 30: ForecastHorizon.DAYS_30}
    horizon = horizon_map.get(request.horizon_days, ForecastHorizon.DAYS_7)

    model_type = None
    if request.model_type:
        try:
            model_type = ModelType(request.model_type)
        except ValueError:
            raise HTTPException(400, f"Invalid model_type: {request.model_type}")

    forecasts = await service.predict_batch(product_ids, horizon, model_type)
    return [PredictionOut(**f.model_dump()) for f in forecasts]
