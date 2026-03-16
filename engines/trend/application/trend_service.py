"""Trend detection service — orchestrates strategies, persistence, and events."""
import asyncio
import logging
from typing import Optional

from ..domain.models import ProductTrendResult, TrendSnapshot
from ..infrastructure.redis_publisher import TrendEventPublisher
from ..infrastructure.trend_repository import TrendRepository
from .trend_aggregator import TrendAggregator

logger = logging.getLogger(__name__)


class TrendService:
    """Application service for trend detection."""

    def __init__(
        self,
        repository: TrendRepository,
        aggregator: TrendAggregator,
        publisher: Optional[TrendEventPublisher] = None,
    ) -> None:
        self._repo = repository
        self._aggregator = aggregator
        self._publisher = publisher

    async def detect_trends(self, limit: int = 100) -> list[ProductTrendResult]:
        """Run trend detection on products with recent activity."""
        product_ids = await self._repo.get_active_product_ids(limit)
        logger.info("Detecting trends for %d products", len(product_ids))

        sem = asyncio.Semaphore(10)
        async def _detect_one(pid: int):
            async with sem:
                try:
                    return await self._detect_single(pid)
                except Exception as e:
                    logger.warning("Trend detection failed for product %d: %s", pid, e)
                    return None

        raw_results = await asyncio.gather(*[_detect_one(pid) for pid in product_ids])
        results = [r for r in raw_results if r is not None]

        logger.info(
            "Trend detection complete: %d results, %d breakouts",
            len(results),
            sum(1 for r in results if r.trend_type == "breakout"),
        )
        return results

    async def _detect_single(self, product_id: int) -> Optional[ProductTrendResult]:
        """Detect trend for a single product."""
        # Build snapshot from current DB state
        snapshot = await self._repo.build_snapshot(product_id)
        if snapshot is None:
            return None

        # Get historical snapshots (from master_products fields)
        snapshots = await self._repo.get_snapshots(product_id)
        snapshots.append(snapshot)

        # Run aggregation
        result = self._aggregator.aggregate(product_id, snapshots)

        # Persist
        await self._repo.save_trend(result)

        # Update master_products trending fields
        await self._repo.update_master_product_trend(product_id, result)

        # Publish event for significant trends
        if self._publisher and result.trend_score >= 50:
            await self._publisher.publish("trend_detected", {
                "product_id": product_id,
                "trend_type": result.trend_type,
                "trend_score": result.trend_score,
                "velocity_ratio": result.velocity_ratio,
            })

        return result

    async def get_trending(self, top_n: int = 20) -> list[ProductTrendResult]:
        """Get top trending products."""
        return await self._repo.get_top_trends(top_n)

    async def get_trend(self, product_id: int) -> Optional[ProductTrendResult]:
        """Get trend for a specific product."""
        return await self._repo.get_trend(product_id)

    async def get_breakouts(self, limit: int = 10) -> list[ProductTrendResult]:
        """Get products with breakout signals."""
        return await self._repo.get_breakouts(limit)
