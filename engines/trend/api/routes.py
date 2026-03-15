"""Trend detection engine API routes."""
import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.database import async_session
from ..application.trend_aggregator import TrendAggregator
from ..application.trend_service import TrendService
from ..domain.strategies import PriceMomentumStrategy, VelocitySpikeStrategy
from ..infrastructure.redis_publisher import TrendEventPublisher
from ..infrastructure.trend_repository import TrendRepository
from .schemas import TrendListOut, TrendOut, TrendScanResult

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/trends", tags=["trends"])


def _get_service() -> TrendService:
    """Factory for TrendService with default dependencies."""
    repo = TrendRepository(async_session)
    aggregator = TrendAggregator([
        VelocitySpikeStrategy(),
        PriceMomentumStrategy(),
    ])
    publisher = None
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            import redis.asyncio as aioredis
            client = aioredis.from_url(redis_url, decode_responses=True)
            publisher = TrendEventPublisher(client)
        except Exception:
            logger.warning("Redis unavailable for trend events")
    return TrendService(repo, aggregator, publisher)


@router.get("/", response_model=TrendListOut)
async def list_trends(
    top_n: int = Query(20, ge=1, le=100),
):
    """Get top trending products."""
    service = _get_service()
    trends = await service.get_trending(top_n)

    # Enrich with product names
    trend_outs = []
    for t in trends:
        out = TrendOut(**t.model_dump())
        trend_outs.append(out)

    return TrendListOut(trends=trend_outs, total=len(trend_outs))


@router.get("/breakouts", response_model=TrendListOut)
async def list_breakouts(limit: int = Query(10, ge=1, le=50)):
    """Get products with breakout signals."""
    service = _get_service()
    breakouts = await service.get_breakouts(limit)
    return TrendListOut(
        trends=[TrendOut(**t.model_dump()) for t in breakouts],
        total=len(breakouts),
    )


@router.get("/{product_id}", response_model=TrendOut)
async def get_trend(product_id: int):
    """Get trend detail for a specific product."""
    service = _get_service()
    trend = await service.get_trend(product_id)
    if trend is None:
        raise HTTPException(404, f"No trend data for product {product_id}")
    return TrendOut(**trend.model_dump())


@router.post("/scan", response_model=TrendScanResult)
async def scan_trends(limit: int = Query(100, ge=1, le=500)):
    """Trigger a trend detection scan."""
    service = _get_service()
    results = await service.detect_trends(limit)
    breakout_count = sum(1 for r in results if r.trend_type == "breakout")
    return TrendScanResult(
        products_analyzed=limit,
        trends_detected=len(results),
        breakouts=breakout_count,
    )
