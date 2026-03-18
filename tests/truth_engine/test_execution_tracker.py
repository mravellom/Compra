"""
Tests — Execution Tracker: full lifecycle from order creation to completion/failure.
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from truth_engine.config import TruthEngineConfig
from truth_engine.execution_tracker import ExecutionTracker
from truth_engine.models import TradeOutcome


class FakeRepository:
    """In-memory fake repository for testing without DB."""

    def __init__(self):
        self._store: dict[int, TradeOutcome] = {}
        self._by_opp: dict[int, TradeOutcome] = {}
        self._next_id = 1

    async def save_outcome(self, outcome: TradeOutcome) -> TradeOutcome:
        if outcome.id is None:
            outcome.id = self._next_id
            self._next_id += 1
        self._store[outcome.id] = outcome
        self._by_opp[outcome.opportunity_id] = outcome
        return outcome

    async def get_by_opportunity(self, opportunity_id: int):
        return self._by_opp.get(opportunity_id)

    async def get_stale_executing(self, timeout_hours: float):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=timeout_hours)
        return [
            o for o in self._store.values()
            if o.executed_at and not o.completed_at
            and not o.sold and not o.cancelled
            and o.executed_at < cutoff
        ]


@pytest.fixture
def tracker():
    repo = FakeRepository()
    cfg = TruthEngineConfig(unsold_timeout_hours=24)
    return ExecutionTracker(repository=repo, config=cfg), repo


class TestOnOrderCreated:

    @pytest.mark.asyncio
    async def test_creates_tracking_entry(self, tracker):
        tracker_svc, repo = tracker
        now = datetime.now(timezone.utc)

        result = await tracker_svc.on_order_created(
            opportunity_id=42,
            detected_at=now - timedelta(hours=1),
            buy_price_predicted=100.0,
            sell_price_predicted=160.0,
            estimated_profit=45.0,
            expected_roi=0.45,
            buy_marketplace="amazon",
            sell_marketplace="mercadolibre_mx",
            master_product_id=99,
        )

        assert result.id is not None
        assert result.opportunity_id == 42
        assert result.estimated_profit == 45.0
        assert result.buy_marketplace == "amazon"
        assert result.executed_at is not None


class TestOnOrderCompleted:

    @pytest.mark.asyncio
    async def test_updates_with_real_prices(self, tracker):
        tracker_svc, repo = tracker
        now = datetime.now(timezone.utc)

        # Create tracking entry
        await tracker_svc.on_order_created(
            opportunity_id=42,
            detected_at=now - timedelta(hours=2),
            buy_price_predicted=100.0,
            sell_price_predicted=160.0,
            estimated_profit=45.0,
            expected_roi=0.45,
        )

        # Complete with real prices
        result = await tracker_svc.on_order_completed(
            opportunity_id=42,
            buy_price_actual=105.0,
            sell_price_actual=155.0,
        )

        assert result is not None
        assert result.sold is True
        assert result.buy_price_actual == 105.0
        assert result.sell_price_actual == 155.0
        assert result.actual_profit == 50.0  # 155 - 105
        assert result.actual_roi == pytest.approx(50.0 / 105.0, abs=0.01)
        assert result.time_to_sell_minutes is not None
        assert result.completed_at is not None

    @pytest.mark.asyncio
    async def test_missing_tracking_returns_none(self, tracker):
        tracker_svc, repo = tracker
        result = await tracker_svc.on_order_completed(
            opportunity_id=999,
            buy_price_actual=100.0,
            sell_price_actual=150.0,
        )
        assert result is None


class TestOnOrderFailed:

    @pytest.mark.asyncio
    async def test_marks_as_failed(self, tracker):
        tracker_svc, repo = tracker
        now = datetime.now(timezone.utc)

        await tracker_svc.on_order_created(
            opportunity_id=42,
            detected_at=now,
            buy_price_predicted=100.0,
            sell_price_predicted=160.0,
            estimated_profit=45.0,
            expected_roi=0.45,
        )

        result = await tracker_svc.on_order_failed(
            opportunity_id=42,
            reason="product mismatch",
        )

        assert result is not None
        assert result.sold is False
        assert result.cancelled is True
        assert result.failure_reason == "product mismatch"
        assert result.actual_profit == 0.0


class TestSweepStale:

    @pytest.mark.asyncio
    async def test_marks_timed_out_as_failed(self, tracker):
        tracker_svc, repo = tracker

        # Create an outcome that was executed 48h ago but never completed
        outcome = TradeOutcome(
            opportunity_id=99,
            detected_at=datetime.now(timezone.utc) - timedelta(hours=50),
            executed_at=datetime.now(timezone.utc) - timedelta(hours=48),
            estimated_profit=30.0,
        )
        await repo.save_outcome(outcome)

        count = await tracker_svc.sweep_stale()
        assert count == 1

        updated = await repo.get_by_opportunity(99)
        assert updated.failure_reason is not None
        assert "timeout" in updated.failure_reason

    @pytest.mark.asyncio
    async def test_does_not_sweep_recent(self, tracker):
        tracker_svc, repo = tracker

        # Executed 1 hour ago — should NOT be swept (timeout = 24h)
        outcome = TradeOutcome(
            opportunity_id=100,
            detected_at=datetime.now(timezone.utc) - timedelta(hours=2),
            executed_at=datetime.now(timezone.utc) - timedelta(hours=1),
            estimated_profit=30.0,
        )
        await repo.save_outcome(outcome)

        count = await tracker_svc.sweep_stale()
        assert count == 0

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, tracker):
        """Create → Complete → Evaluate: full happy path."""
        tracker_svc, repo = tracker
        now = datetime.now(timezone.utc)

        # 1. Order created
        created = await tracker_svc.on_order_created(
            opportunity_id=1,
            detected_at=now - timedelta(hours=3),
            buy_price_predicted=200.0,
            sell_price_predicted=280.0,
            estimated_profit=60.0,
            expected_roi=0.30,
            buy_marketplace="amazon_us",
            sell_marketplace="ebay",
        )
        assert created.sold is False

        # 2. Order completed with actual prices
        completed = await tracker_svc.on_order_completed(
            opportunity_id=1,
            buy_price_actual=205.0,
            sell_price_actual=275.0,
        )
        assert completed.sold is True
        assert completed.actual_profit == 70.0
        assert completed.time_to_sell_minutes > 0

        # 3. Evaluate
        from truth_engine.evaluator import TruthEvaluator
        ev = TruthEvaluator()
        evaluation = ev.evaluate_outcome(completed)
        assert evaluation.success is True
        assert evaluation.profit_error_pct != 0  # Actual differs from estimated
