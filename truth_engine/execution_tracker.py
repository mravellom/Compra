"""
Execution Tracker — bridges the execution engine to truth outcomes.

When an order is created → store tracking entry.
When order completes → update TradeOutcome with real prices + timestamps.
When not sold after timeout → mark as failed.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from .config import TruthEngineConfig
from .models import TradeOutcome
from .repository import TruthRepository

logger = logging.getLogger(__name__)


class ExecutionTracker:
    """Tracks the full lifecycle of trade executions for truth evaluation."""

    def __init__(
        self,
        repository: TruthRepository,
        config: TruthEngineConfig | None = None,
    ) -> None:
        self._repo = repository
        self._cfg = config or TruthEngineConfig()

    async def on_order_created(
        self,
        opportunity_id: int,
        detected_at: datetime,
        buy_price_predicted: float,
        sell_price_predicted: float,
        estimated_profit: float,
        expected_roi: float,
        buy_marketplace: str = "",
        sell_marketplace: str = "",
        master_product_id: Optional[int] = None,
    ) -> TradeOutcome:
        """Record that an opportunity has been sent for execution."""
        outcome = TradeOutcome(
            opportunity_id=opportunity_id,
            detected_at=detected_at,
            executed_at=datetime.now(timezone.utc),
            buy_price_predicted=buy_price_predicted,
            sell_price_predicted=sell_price_predicted,
            estimated_profit=estimated_profit,
            expected_roi=expected_roi,
            buy_marketplace=buy_marketplace,
            sell_marketplace=sell_marketplace,
            master_product_id=master_product_id,
        )
        saved = await self._repo.save_outcome(outcome)

        logger.info(
            "Tracking trade: opp=%d est_profit=$%.2f %s→%s",
            opportunity_id, estimated_profit, buy_marketplace, sell_marketplace,
        )
        return saved

    async def on_order_completed(
        self,
        opportunity_id: int,
        buy_price_actual: float,
        sell_price_actual: float,
    ) -> Optional[TradeOutcome]:
        """Record real execution prices when the trade completes successfully."""
        outcome = await self._repo.get_by_opportunity(opportunity_id)
        if outcome is None:
            logger.warning("No tracking entry for opportunity %d", opportunity_id)
            return None

        now = datetime.now(timezone.utc)

        outcome.completed_at = now
        outcome.buy_price_actual = buy_price_actual
        outcome.sell_price_actual = sell_price_actual
        outcome.actual_profit = sell_price_actual - buy_price_actual
        outcome.actual_roi = (
            outcome.actual_profit / buy_price_actual
            if buy_price_actual > 0 else 0.0
        )
        outcome.sold = True

        # Time to sell: from execution to completion
        if outcome.executed_at:
            delta = now - outcome.executed_at
            outcome.time_to_sell_minutes = delta.total_seconds() / 60.0

        saved = await self._repo.save_outcome(outcome)

        logger.info(
            "Trade completed: opp=%d actual_profit=$%.2f (est=$%.2f) sold_in=%.0fmin",
            opportunity_id,
            outcome.actual_profit,
            outcome.estimated_profit,
            outcome.time_to_sell_minutes or 0,
        )
        return saved

    async def on_order_failed(
        self,
        opportunity_id: int,
        reason: str,
    ) -> Optional[TradeOutcome]:
        """Record a failed trade (cancelled, rejected, or timed out)."""
        outcome = await self._repo.get_by_opportunity(opportunity_id)
        if outcome is None:
            logger.warning("No tracking entry for opportunity %d", opportunity_id)
            return None

        outcome.completed_at = datetime.now(timezone.utc)
        outcome.sold = False
        outcome.cancelled = True
        outcome.failure_reason = reason
        outcome.actual_profit = 0.0
        outcome.actual_roi = 0.0

        saved = await self._repo.save_outcome(outcome)

        logger.info(
            "Trade failed: opp=%d reason=%s",
            opportunity_id, reason,
        )
        return saved

    async def sweep_stale(self) -> int:
        """Mark timed-out executions as failed.

        Called periodically to catch orders that never completed.
        Returns count of outcomes marked as failed.
        """
        stale = await self._repo.get_stale_executing(self._cfg.unsold_timeout_hours)
        count = 0
        for outcome in stale:
            outcome.sold = False
            outcome.failure_reason = (
                f"unsold after {self._cfg.unsold_timeout_hours}h timeout"
            )
            outcome.completed_at = datetime.now(timezone.utc)
            await self._repo.save_outcome(outcome)
            count += 1

        if count > 0:
            logger.info("Swept %d stale executions (timeout=%dh)", count, self._cfg.unsold_timeout_hours)

        return count
