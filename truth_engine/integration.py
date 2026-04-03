"""
Truth Engine Integration — connects the truth engine to execution and detection.

This module is the "glue" that was missing:
1. Hooks ExecutionTracker into the order lifecycle
2. Runs periodic adaptation (evaluate outcomes → adjust thresholds)
3. Exposes adaptive thresholds for the opportunity detector
4. Provides a singleton that the rest of the app uses

Usage in api/main.py:
    from truth_engine.integration import truth_engine_integration
    await truth_engine_integration.start()  # in lifespan startup
    await truth_engine_integration.stop()   # in lifespan shutdown

Usage in opportunity detection:
    from truth_engine.integration import get_adaptive_thresholds
    thresholds = get_adaptive_thresholds()
    # thresholds.min_profit_usd, thresholds.min_roi, etc.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from .adaptive_filters import AdaptiveFilterEngine, AdaptiveFilterState
from .config import TruthEngineConfig
from .decision_gate import DecisionGate
from .evaluator import TruthEvaluator
from .execution_tracker import ExecutionTracker
from .models import PrecisionMetrics
from .repository import TruthRepository

logger = logging.getLogger(__name__)


class TruthEngineIntegration:
    """Singleton integration layer for the truth engine.

    Manages:
    - ExecutionTracker (order lifecycle hooks)
    - AdaptiveFilterEngine (dynamic thresholds)
    - DecisionGate (final execution decision)
    - Periodic adaptation loop
    """

    def __init__(self, config: TruthEngineConfig | None = None) -> None:
        self._cfg = config or TruthEngineConfig()
        self._evaluator = TruthEvaluator()
        self._adaptive = AdaptiveFilterEngine(config=self._cfg)
        self._decision_gate = DecisionGate(config=self._cfg)
        self._tracker: ExecutionTracker | None = None
        self._adaptation_task: asyncio.Task | None = None
        self._running = False
        self._last_metrics: PrecisionMetrics | None = None

    @property
    def adaptive_state(self) -> AdaptiveFilterState:
        return self._adaptive.state

    @property
    def decision_gate(self) -> DecisionGate:
        return self._decision_gate

    @property
    def last_metrics(self) -> PrecisionMetrics | None:
        return self._last_metrics

    # ── Lifecycle ───────────────────────────────────────────

    async def start(self) -> None:
        """Start the periodic adaptation loop."""
        self._running = True
        self._adaptation_task = asyncio.create_task(self._adaptation_loop())
        logger.info(
            "Truth Engine integration started (adaptation every %d min, target precision=%.0f%%)",
            self._cfg.adaptation_window_days,  # reuses the window param for log
            self._cfg.precision_target * 100,
        )

    async def stop(self) -> None:
        self._running = False
        if self._adaptation_task:
            self._adaptation_task.cancel()

    # ── Order Lifecycle Hooks ───────────────────────────────

    async def on_order_executed(
        self,
        opportunity_id: int,
        detected_at: datetime,
        buy_price_predicted: float,
        sell_price_predicted: float,
        estimated_profit: float,
        expected_roi: float,
        buy_marketplace: str = "",
        sell_marketplace: str = "",
        master_product_id: int | None = None,
    ) -> None:
        """Called when an order is executed. Records tracking entry."""
        tracker = await self._get_tracker()
        if tracker is None:
            return

        try:
            await tracker.on_order_created(
                opportunity_id=opportunity_id,
                detected_at=detected_at,
                buy_price_predicted=buy_price_predicted,
                sell_price_predicted=sell_price_predicted,
                estimated_profit=estimated_profit,
                expected_roi=expected_roi,
                buy_marketplace=buy_marketplace,
                sell_marketplace=sell_marketplace,
                master_product_id=master_product_id,
            )
        except Exception as e:
            logger.error("Truth engine on_order_executed error: %s", e, exc_info=True)

    async def on_order_completed(
        self,
        opportunity_id: int,
        buy_price_actual: float,
        sell_price_actual: float,
    ) -> None:
        """Called when a trade completes successfully."""
        tracker = await self._get_tracker()
        if tracker is None:
            return

        try:
            outcome = await tracker.on_order_completed(
                opportunity_id=opportunity_id,
                buy_price_actual=buy_price_actual,
                sell_price_actual=sell_price_actual,
            )
            if outcome:
                logger.info(
                    "Truth: trade completed opp=%d profit=$%.2f (est=$%.2f)",
                    opportunity_id, outcome.actual_profit, outcome.estimated_profit,
                )
        except Exception as e:
            logger.error("Truth engine on_order_completed error: %s", e, exc_info=True)

    async def on_order_failed(
        self,
        opportunity_id: int,
        reason: str,
    ) -> None:
        """Called when a trade fails or is cancelled."""
        tracker = await self._get_tracker()
        if tracker is None:
            return

        try:
            await tracker.on_order_failed(opportunity_id=opportunity_id, reason=reason)
        except Exception as e:
            logger.error("Truth engine on_order_failed error: %s", e, exc_info=True)

    # ── Adaptation Loop ─────────────────────────────────────

    async def _adaptation_loop(self) -> None:
        """Periodically evaluate outcomes and adapt thresholds.

        Runs every 30 minutes:
        1. Sweep stale executions (timed out)
        2. Fetch all executed outcomes in window
        3. Compute precision metrics
        4. Adapt thresholds (tighten if precision low, relax if high)
        """
        adaptation_interval = int(
            float(__import__("os").getenv("TRUTH_ADAPTATION_INTERVAL", "1800"))
        )
        await asyncio.sleep(60)  # Wait for startup

        while self._running:
            try:
                await self._run_adaptation_cycle()
            except Exception as e:
                logger.error("Adaptation cycle error: %s", e, exc_info=True)

            await asyncio.sleep(adaptation_interval)

    async def _run_adaptation_cycle(self) -> None:
        """Execute one adaptation cycle."""
        from api.database import async_session

        async with async_session() as db:
            repo = TruthRepository(db)
            tracker = ExecutionTracker(repo, self._cfg)

            # 1. Sweep stale executions
            swept = await tracker.sweep_stale()
            if swept > 0:
                logger.info("Swept %d stale executions", swept)

            # 2. Get outcomes
            outcomes = await repo.get_executed_outcomes(
                window_days=self._cfg.adaptation_window_days
            )
            total_detected = await repo.count_detected(
                window_days=self._cfg.adaptation_window_days
            )

            # 3. Compute precision
            metrics = self._evaluator.compute_precision_metrics(
                outcomes=outcomes,
                total_detected=total_detected,
                window_days=self._cfg.adaptation_window_days,
            )
            self._last_metrics = metrics

            # 4. Adapt thresholds
            old_state = AdaptiveFilterState(
                min_profit_usd=self._adaptive.state.min_profit_usd,
                min_roi=self._adaptive.state.min_roi,
            )
            self._adaptive.adapt(metrics)

            # Log changes
            if (old_state.min_profit_usd != self._adaptive.state.min_profit_usd or
                    old_state.min_roi != self._adaptive.state.min_roi):
                logger.info(
                    "Thresholds adapted: profit $%.1f→$%.1f, ROI %.0f%%→%.0f%% "
                    "(precision=%.2f, target=%.2f)",
                    old_state.min_profit_usd, self._adaptive.state.min_profit_usd,
                    old_state.min_roi * 100, self._adaptive.state.min_roi * 100,
                    metrics.precision, self._cfg.precision_target,
                )

            await db.commit()

    # ── Helper ──────────────────────────────────────────────

    async def _get_tracker(self) -> ExecutionTracker | None:
        """Get or create an ExecutionTracker with a DB session."""
        try:
            from api.database import async_session
            db = async_session()
            repo = TruthRepository(db)
            return ExecutionTracker(repo, self._cfg)
        except Exception as e:
            logger.error("Cannot create ExecutionTracker: %s", e)
            return None


# ── Singleton ───────────────────────────────────────────────
truth_engine_integration = TruthEngineIntegration()


def get_adaptive_thresholds() -> AdaptiveFilterState:
    """Get current adaptive thresholds for use in opportunity detection.

    Returns the dynamically adjusted thresholds. If the truth engine
    hasn't adapted yet, returns default values.
    """
    return truth_engine_integration.adaptive_state


def get_decision_gate() -> DecisionGate:
    """Get the decision gate for final execution scoring."""
    return truth_engine_integration.decision_gate


def get_last_precision() -> float:
    """Get the last computed precision (0-1). Returns 0.0 if no data yet."""
    metrics = truth_engine_integration.last_metrics
    return metrics.precision if metrics else 0.0
