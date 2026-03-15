"""Repository for prediction persistence using SQLAlchemy async."""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..domain.models import PriceForecast, PricePoint

logger = logging.getLogger(__name__)


class PredictionRepository:
    """Handles all DB operations for the prediction engine."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save_prediction(self, forecast: PriceForecast) -> None:
        """Upsert a prediction (unique on product_id + model_type + horizon_days)."""
        async with self._session_factory() as session:
            stmt = pg_insert(
                _predictions_table()
            ).values(
                product_id=forecast.product_id,
                model_type=forecast.model_type,
                horizon_days=forecast.horizon_days,
                predicted_price=forecast.predicted_price,
                confidence_lower=forecast.confidence_lower,
                confidence_upper=forecast.confidence_upper,
                mape=forecast.mape,
                confidence=forecast.confidence,
                features_used=json.dumps(forecast.features_used),
                status=forecast.status,
            ).on_conflict_do_update(
                constraint="uq_prediction_product_model_horizon",
                set_={
                    "predicted_price": forecast.predicted_price,
                    "confidence_lower": forecast.confidence_lower,
                    "confidence_upper": forecast.confidence_upper,
                    "mape": forecast.mape,
                    "confidence": forecast.confidence,
                    "features_used": json.dumps(forecast.features_used),
                    "status": forecast.status,
                    "created_at": text("now()"),
                },
            )
            await session.execute(stmt)
            await session.commit()

    async def get_predictions(
        self, product_id: int, horizon: Optional[int] = None
    ) -> list[PriceForecast]:
        """Get stored predictions for a product."""
        from api.models import PricePrediction

        async with self._session_factory() as session:
            stmt = select(PricePrediction).where(
                PricePrediction.product_id == product_id
            )
            if horizon:
                stmt = stmt.where(PricePrediction.horizon_days == horizon)
            stmt = stmt.order_by(PricePrediction.created_at.desc())

            result = await session.execute(stmt)
            rows = result.scalars().all()

            return [
                PriceForecast(
                    id=r.id,
                    product_id=r.product_id,
                    model_type=r.model_type,
                    horizon_days=r.horizon_days,
                    predicted_price=float(r.predicted_price),
                    confidence_lower=float(r.confidence_lower) if r.confidence_lower else None,
                    confidence_upper=float(r.confidence_upper) if r.confidence_upper else None,
                    mape=r.mape,
                    confidence=r.confidence,
                    features_used=json.loads(r.features_used) if r.features_used else {},
                    status=r.status,
                    created_at=r.created_at,
                )
                for r in rows
            ]

    async def get_price_history(
        self, product_id: int, days: int = 90
    ) -> list[PricePoint]:
        """Fetch price history for a product from price_history + product_listings."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        async with self._session_factory() as session:
            # Join through product_listings to get listing URLs, then price_history
            result = await session.execute(
                text("""
                    SELECT ph.price, ph.recorded_at
                    FROM price_history ph
                    JOIN product_listings pl ON ph.listing_url = pl.url
                    WHERE pl.master_product_id = :product_id
                      AND ph.recorded_at >= :cutoff
                    ORDER BY ph.recorded_at ASC
                """),
                {"product_id": product_id, "cutoff": cutoff},
            )
            rows = result.fetchall()

            return [
                PricePoint(price=float(row[0]), recorded_at=row[1])
                for row in rows
            ]

    async def get_products_with_history(self, limit: int = 50) -> list[int]:
        """Get product IDs that have sufficient price history for prediction."""
        async with self._session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT pl.master_product_id, COUNT(DISTINCT ph.id) AS cnt
                    FROM product_listings pl
                    JOIN price_history ph ON ph.listing_url = pl.url
                    WHERE ph.recorded_at >= now() - interval '90 days'
                    GROUP BY pl.master_product_id
                    HAVING COUNT(DISTINCT ph.id) >= 5
                    ORDER BY cnt DESC
                    LIMIT :limit
                """),
                {"limit": limit},
            )
            return [row[0] for row in result.fetchall()]


def _predictions_table():
    """Get the price_predictions table object for raw inserts."""
    from api.models import PricePrediction
    return PricePrediction.__table__
