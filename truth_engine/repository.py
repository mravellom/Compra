"""
TruthRepository — persistence layer for trade outcomes.

Uses SQLAlchemy async, following the same pattern as api/database.py.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import TradeOutcome as TradeOutcomeORM
from .models import TradeOutcome

logger = logging.getLogger(__name__)


def _domain_to_orm(outcome: TradeOutcome) -> TradeOutcomeORM:
    """Convert domain model to ORM model."""
    orm = TradeOutcomeORM(
        opportunity_id=outcome.opportunity_id,
        detected_at=outcome.detected_at,
        executed_at=outcome.executed_at,
        completed_at=outcome.completed_at,
        buy_price_predicted=outcome.buy_price_predicted,
        sell_price_predicted=outcome.sell_price_predicted,
        estimated_profit=outcome.estimated_profit,
        expected_roi=outcome.expected_roi,
        buy_price_actual=outcome.buy_price_actual,
        sell_price_actual=outcome.sell_price_actual,
        actual_profit=outcome.actual_profit,
        actual_roi=outcome.actual_roi,
        time_to_sell_minutes=outcome.time_to_sell_minutes,
        sold=outcome.sold,
        cancelled=outcome.cancelled,
        failure_reason=outcome.failure_reason,
        buy_marketplace=outcome.buy_marketplace,
        sell_marketplace=outcome.sell_marketplace,
        master_product_id=outcome.master_product_id,
    )
    if outcome.id is not None:
        orm.id = outcome.id
    return orm


def _orm_to_domain(orm: TradeOutcomeORM) -> TradeOutcome:
    """Convert ORM model to domain model."""
    return TradeOutcome(
        id=orm.id,
        opportunity_id=orm.opportunity_id,
        detected_at=orm.detected_at,
        executed_at=orm.executed_at,
        completed_at=orm.completed_at,
        buy_price_predicted=float(orm.buy_price_predicted or 0),
        sell_price_predicted=float(orm.sell_price_predicted or 0),
        estimated_profit=float(orm.estimated_profit or 0),
        expected_roi=float(orm.expected_roi or 0),
        buy_price_actual=float(orm.buy_price_actual or 0),
        sell_price_actual=float(orm.sell_price_actual or 0),
        actual_profit=float(orm.actual_profit or 0),
        actual_roi=float(orm.actual_roi or 0),
        time_to_sell_minutes=float(orm.time_to_sell_minutes) if orm.time_to_sell_minutes else None,
        sold=orm.sold,
        cancelled=orm.cancelled,
        failure_reason=orm.failure_reason,
        buy_marketplace=orm.buy_marketplace or "",
        sell_marketplace=orm.sell_marketplace or "",
        master_product_id=orm.master_product_id,
    )


class TruthRepository:
    """Async repository for trade outcomes."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def save_outcome(self, outcome: TradeOutcome) -> TradeOutcome:
        """Insert or update a trade outcome."""
        if outcome.id is not None:
            # Update existing
            result = await self._db.execute(
                select(TradeOutcomeORM).where(TradeOutcomeORM.id == outcome.id)
            )
            existing = result.scalars().first()
            if existing:
                for attr in (
                    "executed_at", "completed_at",
                    "buy_price_actual", "sell_price_actual",
                    "actual_profit", "actual_roi",
                    "time_to_sell_minutes", "sold", "cancelled", "failure_reason",
                ):
                    setattr(existing, attr, getattr(outcome, attr))
                await self._db.flush()
                logger.info("Updated trade outcome id=%d opp=%d", existing.id, existing.opportunity_id)
                return _orm_to_domain(existing)

        orm = _domain_to_orm(outcome)
        self._db.add(orm)
        await self._db.flush()
        logger.info("Saved new trade outcome id=%d opp=%d", orm.id, orm.opportunity_id)
        return _orm_to_domain(orm)

    async def get_outcome(self, outcome_id: int) -> Optional[TradeOutcome]:
        result = await self._db.execute(
            select(TradeOutcomeORM).where(TradeOutcomeORM.id == outcome_id)
        )
        orm = result.scalars().first()
        return _orm_to_domain(orm) if orm else None

    async def get_by_opportunity(self, opportunity_id: int) -> Optional[TradeOutcome]:
        result = await self._db.execute(
            select(TradeOutcomeORM)
            .where(TradeOutcomeORM.opportunity_id == opportunity_id)
            .order_by(TradeOutcomeORM.detected_at.desc())
            .limit(1)
        )
        orm = result.scalars().first()
        return _orm_to_domain(orm) if orm else None

    async def get_outcomes_by_date(
        self, start: datetime, end: datetime,
    ) -> list[TradeOutcome]:
        result = await self._db.execute(
            select(TradeOutcomeORM)
            .where(
                TradeOutcomeORM.detected_at >= start,
                TradeOutcomeORM.detected_at <= end,
            )
            .order_by(TradeOutcomeORM.detected_at.desc())
        )
        return [_orm_to_domain(r) for r in result.scalars().all()]

    async def get_outcomes_by_product(
        self, product_id: int, limit: int = 50,
    ) -> list[TradeOutcome]:
        result = await self._db.execute(
            select(TradeOutcomeORM)
            .where(TradeOutcomeORM.master_product_id == product_id)
            .order_by(TradeOutcomeORM.detected_at.desc())
            .limit(limit)
        )
        return [_orm_to_domain(r) for r in result.scalars().all()]

    async def get_executed_outcomes(
        self, window_days: int = 30,
    ) -> list[TradeOutcome]:
        """Get all outcomes where execution was attempted (sold or failed)."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        result = await self._db.execute(
            select(TradeOutcomeORM)
            .where(
                TradeOutcomeORM.executed_at.is_not(None),
                TradeOutcomeORM.detected_at >= cutoff,
            )
            .order_by(TradeOutcomeORM.detected_at.desc())
        )
        return [_orm_to_domain(r) for r in result.scalars().all()]

    async def get_stale_executing(
        self, timeout_hours: float,
    ) -> list[TradeOutcome]:
        """Find outcomes that were executed but never completed (timed out)."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=timeout_hours)
        result = await self._db.execute(
            select(TradeOutcomeORM)
            .where(
                TradeOutcomeORM.executed_at.is_not(None),
                TradeOutcomeORM.completed_at.is_(None),
                TradeOutcomeORM.sold.is_(False),
                TradeOutcomeORM.cancelled.is_(False),
                TradeOutcomeORM.executed_at < cutoff,
            )
        )
        return [_orm_to_domain(r) for r in result.scalars().all()]

    async def count_detected(self, window_days: int = 30) -> int:
        """Count total opportunities detected in window (for precision calc)."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        result = await self._db.execute(
            select(func.count(TradeOutcomeORM.id))
            .where(TradeOutcomeORM.detected_at >= cutoff)
        )
        return result.scalar() or 0
