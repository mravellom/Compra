"""
Portfolio Optimizer Service — application layer.

Sits between the Orchestrator and the Execution Bridge in the pipeline:

  Orchestrator → Redis(orchestrator_events)
       ↓
  PortfolioOptimizerService (consumes, batches, optimizes)
       ↓
  Redis(execution_selected) → ExecutionBridge

Design:
  - Collects execution_recommended events into a batch window
  - Runs the optimizer on the batch
  - Publishes execution_selected events only for approved candidates
  - Tracks metrics for observability
"""
import asyncio
import json
import logging
import os
import time
from typing import Optional

import redis.asyncio as aioredis

from ..domain.models import (
    OpportunityCandidate,
    OptimizationResult,
    PortfolioState,
)
from ..domain.optimizer import (
    DiversificationConfig,
    PortfolioOptimizer,
    ScoringWeights,
)

logger = logging.getLogger(__name__)

# Redis streams
INPUT_STREAM = "orchestrator_events"
OUTPUT_STREAM = "execution_selected"
GROUP_NAME = "portfolio_optimizer"
CONSUMER_NAME = os.getenv("OPTIMIZER_CONSUMER_NAME", "optimizer-1")

# Batching config
BATCH_WINDOW_MS = int(os.getenv("OPT_BATCH_WINDOW_MS", "3000"))
BATCH_MAX_SIZE = int(os.getenv("OPT_BATCH_MAX_SIZE", "500"))

# Portfolio defaults
DEFAULT_CAPITAL = float(os.getenv("OPT_TOTAL_CAPITAL", "10000"))
MAX_RISK = float(os.getenv("OPT_MAX_RISK", "70"))
MAX_POSITIONS = int(os.getenv("OPT_MAX_POSITIONS", "20"))


class OptimizerMetrics:
    """Entregable 7 — observable metrics for the optimizer."""
    __slots__ = (
        "opportunities_received", "opportunities_selected",
        "total_profit_estimate", "rejection_reasons",
        "batches_processed", "last_batch_ms",
    )

    def __init__(self) -> None:
        self.opportunities_received: int = 0
        self.opportunities_selected: int = 0
        self.total_profit_estimate: float = 0.0
        self.rejection_reasons: dict[str, int] = {}
        self.batches_processed: int = 0
        self.last_batch_ms: float = 0.0

    def record_batch(self, result: OptimizationResult, elapsed_ms: float) -> None:
        self.opportunities_received += len(result.approved) + len(result.rejected)
        self.opportunities_selected += len(result.approved)
        self.total_profit_estimate += result.total_expected_profit
        self.batches_processed += 1
        self.last_batch_ms = elapsed_ms
        for reason, count in result.rejection_summary.items():
            self.rejection_reasons[reason] = (
                self.rejection_reasons.get(reason, 0) + count
            )

    def snapshot(self) -> dict:
        return {
            "optimizer_opportunities_received": self.opportunities_received,
            "optimizer_opportunities_selected": self.opportunities_selected,
            "optimizer_profit_estimate": round(self.total_profit_estimate, 2),
            "optimizer_rejection_reasons": dict(self.rejection_reasons),
            "optimizer_batches_processed": self.batches_processed,
            "optimizer_last_batch_ms": round(self.last_batch_ms, 2),
        }


class PortfolioOptimizerService:
    """Application service — consumes, optimizes, publishes.

    Lifecycle:
      1. start() begins consuming orchestrator_events
      2. Events with type=execution_recommended are collected into a batch
      3. After BATCH_WINDOW_MS or BATCH_MAX_SIZE, batch is optimized
      4. Approved candidates are published to execution_selected stream
      5. stop() gracefully shuts down
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        optimizer: PortfolioOptimizer | None = None,
        capital: float = DEFAULT_CAPITAL,
        max_risk: float = MAX_RISK,
        max_positions: int = MAX_POSITIONS,
    ) -> None:
        self._redis = redis_client
        self._optimizer = optimizer or PortfolioOptimizer()
        self._capital = capital
        self._max_risk = max_risk
        self._max_positions = max_positions
        self._running = False
        self._metrics = OptimizerMetrics()
        self._batch: list[OpportunityCandidate] = []

    @property
    def metrics(self) -> OptimizerMetrics:
        return self._metrics

    async def start(self) -> None:
        """Start the optimizer consumer loop."""
        self._running = True
        await self._ensure_group()
        logger.info(
            "[PortfolioOptimizer] Started (capital=$%.0f, max_positions=%d, batch_window=%dms)",
            self._capital, self._max_positions, BATCH_WINDOW_MS,
        )

        while self._running:
            try:
                messages = await self._redis.xreadgroup(
                    groupname=GROUP_NAME,
                    consumername=CONSUMER_NAME,
                    streams={INPUT_STREAM: ">"},
                    count=BATCH_MAX_SIZE,
                    block=BATCH_WINDOW_MS,
                )

                if messages:
                    for _, entries in messages:
                        for msg_id, fields in entries:
                            self._collect(fields)
                            await self._redis.xack(INPUT_STREAM, GROUP_NAME, msg_id)

                # Flush batch if we have candidates (window expired or max size)
                if self._batch:
                    await self._flush_batch()

            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("[PortfolioOptimizer] Error in consumer loop", exc_info=True)
                await asyncio.sleep(2)

        # Flush remaining on shutdown
        if self._batch:
            await self._flush_batch()

        logger.info(
            "[PortfolioOptimizer] Stopped. %s", self._metrics.snapshot(),
        )

    async def stop(self) -> None:
        self._running = False

    def optimize_batch(
        self,
        candidates: list[OpportunityCandidate],
        active_positions: int = 0,
    ) -> OptimizationResult:
        """Run optimization on a batch synchronously (for testing/direct use).

        This is the pure domain operation — no Redis dependency.
        """
        state = PortfolioState(
            total_capital=self._capital,
            available_capital=self._capital,
            max_risk_per_trade=self._max_risk,
            max_open_positions=self._max_positions,
            active_positions=active_positions,
        )
        return self._optimizer.optimize(candidates, state)

    # ── Internal ──────────────────────────────────────────

    def _collect(self, fields: dict) -> None:
        """Parse and collect an execution_recommended event."""
        event_type = fields.get("event_type", "")
        if event_type != "execution_recommended":
            return

        try:
            data = json.loads(fields.get("data", "{}"))
            candidate = OpportunityCandidate(
                opportunity_id=data.get("opportunity_id", 0),
                product_id=data.get("product_id", 0),
                buy_marketplace=data.get("buy_marketplace", ""),
                sell_marketplace=data.get("sell_marketplace", ""),
                buy_price=data.get("buy_price", data.get("recommended_price", 0)),
                sell_price=data.get("sell_price", 0),
                expected_profit=data.get("net_profit", 0),
                confidence=data.get("confidence_score", 50),
                risk_score=data.get("risk_score", 50),
                capital_required=data.get("buy_price", data.get("recommended_price", 0)),
                velocity_score=data.get("velocity_score", 50),
                score=data.get("score", 0),
                signal_strength=data.get("signal_strength", "moderate"),
            )
            if candidate.capital_required > 0:
                self._batch.append(candidate)
        except Exception:
            logger.warning("[PortfolioOptimizer] Failed to parse event", exc_info=True)

    async def _flush_batch(self) -> None:
        """Optimize the current batch and publish approved candidates."""
        batch = self._batch
        self._batch = []

        t0 = time.monotonic()
        result = self.optimize_batch(batch)
        elapsed_ms = (time.monotonic() - t0) * 1000

        self._metrics.record_batch(result, elapsed_ms)

        # Publish approved candidates to execution_selected stream
        for candidate in result.approved:
            await self._publish_selected(candidate)

        if result.approved:
            logger.info(
                "[PortfolioOptimizer] Batch: %d/%d selected, profit=$%.2f, %.1fms",
                len(result.approved), len(batch),
                result.total_expected_profit, elapsed_ms,
            )

    async def _publish_selected(self, candidate: OpportunityCandidate) -> None:
        """Publish an approved candidate to the execution_selected stream."""
        try:
            event = {
                "event_type": "execution_selected",
                "data": json.dumps({
                    "opportunity_id": candidate.opportunity_id,
                    "product_id": candidate.product_id,
                    "buy_marketplace": candidate.buy_marketplace,
                    "sell_marketplace": candidate.sell_marketplace,
                    "buy_price": candidate.buy_price,
                    "expected_profit": candidate.expected_profit,
                    "capital_required": candidate.capital_required,
                    "execution_mode": (
                        "auto" if candidate.signal_strength == "strong" and candidate.score >= 80
                        else "assisted" if candidate.score >= 65
                        else "manual"
                    ),
                    "score": candidate.score,
                    "signal_strength": candidate.signal_strength,
                }),
            }
            await self._redis.xadd(OUTPUT_STREAM, event)
        except Exception:
            logger.error(
                "[PortfolioOptimizer] Failed to publish candidate %d",
                candidate.opportunity_id, exc_info=True,
            )

    async def _ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(
                INPUT_STREAM, GROUP_NAME, id="0", mkstream=True,
            )
        except aioredis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
