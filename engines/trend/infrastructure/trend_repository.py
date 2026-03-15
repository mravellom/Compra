"""Repository for trend persistence using SQLAlchemy async."""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..domain.models import ProductTrendResult, TrendSnapshot

logger = logging.getLogger(__name__)


class TrendRepository:
    """Handles all DB operations for the trend engine."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_active_product_ids(self, limit: int = 100) -> list[int]:
        """Get products with recent listing activity (candidates for trend detection)."""
        async with self._session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT id FROM master_products
                    WHERE listing_count > 0
                      AND updated_at >= now() - interval '7 days'
                    ORDER BY listing_count DESC
                    LIMIT :limit
                """),
                {"limit": limit},
            )
            return [row[0] for row in result.fetchall()]

    async def build_snapshot(self, product_id: int) -> Optional[TrendSnapshot]:
        """Build a current snapshot from master_products data."""
        async with self._session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT listing_count, avg_price, seller_count,
                           marketplace_count, velocity_7d, velocity_30d
                    FROM master_products
                    WHERE id = :pid
                """),
                {"pid": product_id},
            )
            row = result.fetchone()
            if not row:
                return None

            return TrendSnapshot(
                product_id=product_id,
                listing_count=row[0] or 0,
                avg_price=float(row[1] or 0),
                seller_count=row[2] or 0,
                marketplace_count=row[3] or 0,
                velocity_7d=float(row[4] or 0),
                velocity_30d=float(row[5] or 0),
            )

    async def get_snapshots(self, product_id: int) -> list[TrendSnapshot]:
        """Get historical snapshots for trend computation.

        Since we don't store a time-series of snapshots,
        we reconstruct from opportunity_history + current state.
        """
        async with self._session_factory() as session:
            # Use opportunity_history as a proxy for historical state
            result = await session.execute(
                text("""
                    SELECT DISTINCT ON (date_trunc('day', recorded_at))
                           recorded_at,
                           competitor_count,
                           buy_price
                    FROM opportunity_history
                    WHERE master_product_id = :pid
                    ORDER BY date_trunc('day', recorded_at), recorded_at DESC
                    LIMIT 30
                """),
                {"pid": product_id},
            )
            rows = result.fetchall()

            # Build synthetic snapshots from available data
            snapshots = []
            for row in rows:
                snapshots.append(TrendSnapshot(
                    product_id=product_id,
                    listing_count=row[1] or 0,  # use competitor_count as proxy
                    avg_price=float(row[2] or 0),
                    seller_count=0,
                    marketplace_count=0,
                    velocity_7d=0,
                    velocity_30d=0,
                    snapshot_at=row[0],
                ))

            return snapshots

    async def save_trend(self, result: ProductTrendResult) -> None:
        """Upsert a trend result."""
        from api.models import ProductTrend as ProductTrendModel

        async with self._session_factory() as session:
            stmt = pg_insert(
                ProductTrendModel.__table__
            ).values(
                product_id=result.product_id,
                trend_score=result.trend_score,
                velocity_ratio=result.velocity_ratio,
                price_momentum=result.price_momentum,
                volume_change=result.volume_change,
                trend_type=result.trend_type,
                trend_strength=result.trend_strength,
                signals=json.dumps(result.signals),
            ).on_conflict_do_update(
                constraint="uq_trend_product",
                set_={
                    "trend_score": result.trend_score,
                    "velocity_ratio": result.velocity_ratio,
                    "price_momentum": result.price_momentum,
                    "volume_change": result.volume_change,
                    "trend_type": result.trend_type,
                    "trend_strength": result.trend_strength,
                    "signals": json.dumps(result.signals),
                    "detected_at": text("now()"),
                },
            )
            await session.execute(stmt)
            await session.commit()

    async def update_master_product_trend(
        self, product_id: int, result: ProductTrendResult
    ) -> None:
        """Update trending fields on master_products."""
        async with self._session_factory() as session:
            await session.execute(
                text("""
                    UPDATE master_products
                    SET trend_score = :score,
                        trend_label = :label,
                        is_trending = :is_trending,
                        last_snapshot_at = now()
                    WHERE id = :pid
                """),
                {
                    "pid": product_id,
                    "score": result.trend_score,
                    "label": result.trend_type,
                    "is_trending": result.trend_score >= 50,
                },
            )
            await session.commit()

    async def get_top_trends(self, top_n: int = 20) -> list[ProductTrendResult]:
        """Get top trending products."""
        from api.models import ProductTrend as ProductTrendModel

        async with self._session_factory() as session:
            stmt = (
                select(ProductTrendModel)
                .order_by(ProductTrendModel.trend_score.desc())
                .limit(top_n)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [self._to_domain(r) for r in rows]

    async def get_trend(self, product_id: int) -> Optional[ProductTrendResult]:
        """Get trend for a specific product."""
        from api.models import ProductTrend as ProductTrendModel

        async with self._session_factory() as session:
            stmt = select(ProductTrendModel).where(
                ProductTrendModel.product_id == product_id
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._to_domain(row) if row else None

    async def get_breakouts(self, limit: int = 10) -> list[ProductTrendResult]:
        """Get products with breakout trends."""
        from api.models import ProductTrend as ProductTrendModel

        async with self._session_factory() as session:
            stmt = (
                select(ProductTrendModel)
                .where(ProductTrendModel.trend_type == "breakout")
                .order_by(ProductTrendModel.trend_score.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [self._to_domain(r) for r in rows]

    @staticmethod
    def _to_domain(row) -> ProductTrendResult:
        return ProductTrendResult(
            id=row.id,
            product_id=row.product_id,
            trend_score=row.trend_score,
            velocity_ratio=row.velocity_ratio,
            price_momentum=row.price_momentum,
            volume_change=row.volume_change,
            trend_type=row.trend_type,
            trend_strength=row.trend_strength,
            signals=json.loads(row.signals) if row.signals else {},
            detected_at=row.detected_at,
        )
