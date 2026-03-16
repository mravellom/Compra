"""
Execution Bridge — connects Portfolio Optimizer to Execution Engine.

Consumes events from Redis Stream and automatically creates
trade orders for selected opportunities.

Pipeline position:
  Orchestrator → PortfolioOptimizer → Redis(execution_selected) → ExecutionBridge → ExecutionService

Falls back to consuming orchestrator_events directly if no optimizer is running.

Design patterns:
  - Consumer/Producer (Redis Streams)
  - Bridge (connects two bounded contexts)
  - Strategy (execution mode selection based on signal strength)
"""
import asyncio
import json
import logging
import os
from typing import Optional

import redis.asyncio as aioredis

from ..domain.enums import ExecutionMode, OrderType
from .execution_service import ExecutionService

logger = logging.getLogger(__name__)

# Primary: consume optimizer-selected events; fallback: raw orchestrator events
STREAM_NAME = os.getenv("BRIDGE_INPUT_STREAM", "execution_selected")
GROUP_NAME = "execution_bridge"
CONSUMER_NAME = os.getenv("BRIDGE_CONSUMER_NAME", "bridge-1")

# Thresholds for auto-creating orders from orchestrator decisions
MIN_DECISION_SCORE = float(os.getenv("BRIDGE_MIN_SCORE", "60"))
MIN_SIGNAL_STRENGTH = os.getenv("BRIDGE_MIN_SIGNAL", "moderate")

_STRENGTH_ORDER = {"weak": 0, "moderate": 1, "strong": 2}


class ExecutionBridge:
    """Bridges orchestrator EXECUTE decisions into trade orders.

    Reads orchestrator_events from Redis Streams, filters for
    execution_recommended events, and creates orders via ExecutionService.
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        execution_service: ExecutionService,
    ) -> None:
        self._redis = redis_client
        self._service = execution_service
        self._running = False
        self._stats = {"consumed": 0, "orders_created": 0, "skipped": 0, "errors": 0}

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    async def start(self) -> None:
        """Start consuming orchestrator events."""
        self._running = True
        await self._ensure_group()
        logger.info("[ExecutionBridge] Started consuming %s", STREAM_NAME)

        while self._running:
            try:
                messages = await self._redis.xreadgroup(
                    groupname=GROUP_NAME,
                    consumername=CONSUMER_NAME,
                    streams={STREAM_NAME: ">"},
                    count=10,
                    block=2000,
                )
                if not messages:
                    continue

                for stream_name, entries in messages:
                    for msg_id, fields in entries:
                        await self._process_message(msg_id, fields)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("[ExecutionBridge] Error reading stream", exc_info=True)
                await asyncio.sleep(2)

        logger.info("[ExecutionBridge] Stopped. Stats: %s", self._stats)

    async def stop(self) -> None:
        self._running = False

    async def _ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(
                STREAM_NAME, GROUP_NAME, id="0", mkstream=True,
            )
        except aioredis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def _process_message(self, msg_id: str, fields: dict) -> None:
        """Process a single orchestrator event."""
        try:
            event_type = fields.get("event_type", "")
            if event_type not in ("execution_recommended", "execution_selected"):
                await self._redis.xack(STREAM_NAME, GROUP_NAME, msg_id)
                return

            data = json.loads(fields.get("data", "{}"))
            self._stats["consumed"] += 1

            order = await self._handle_execution_recommended(data)
            if order:
                self._stats["orders_created"] += 1
                logger.info(
                    "[ExecutionBridge] Created order #%s for opp #%s (score=%.0f, mode=%s)",
                    order.id, order.opportunity_id,
                    data.get("score", 0), order.execution_mode.value,
                )
            else:
                self._stats["skipped"] += 1

            await self._redis.xack(STREAM_NAME, GROUP_NAME, msg_id)

        except Exception:
            self._stats["errors"] += 1
            logger.error(
                "[ExecutionBridge] Failed to process %s", msg_id, exc_info=True,
            )
            await self._redis.xack(STREAM_NAME, GROUP_NAME, msg_id)

    async def _handle_execution_recommended(self, data: dict):
        """Create an execution order from an orchestrator EXECUTE decision.

        Expected data keys (from OrchestratorEventPublisher):
          - opportunity_id: int
          - product_id: int
          - score: float (decision score 0-100)
          - signal_strength: str (weak/moderate/strong)
          - recommended_price: float (optional)
          - recommended_action: str (optional)
          - buy_marketplace: str
          - sell_marketplace: str
          - net_profit: float
          - roi: float
        """
        score = data.get("score", 0)
        strength = data.get("signal_strength", "weak")

        # Filter: only process high-confidence decisions
        if score < MIN_DECISION_SCORE:
            logger.debug(
                "[ExecutionBridge] Skipping opp #%s: score %.0f < %.0f",
                data.get("opportunity_id"), score, MIN_DECISION_SCORE,
            )
            return None

        strength_val = _STRENGTH_ORDER.get(strength, 0)
        min_strength_val = _STRENGTH_ORDER.get(MIN_SIGNAL_STRENGTH, 1)
        if strength_val < min_strength_val:
            logger.debug(
                "[ExecutionBridge] Skipping opp #%s: signal %s < %s",
                data.get("opportunity_id"), strength, MIN_SIGNAL_STRENGTH,
            )
            return None

        # Determine execution mode based on signal strength + score
        if strength == "strong" and score >= 80:
            mode = ExecutionMode.AUTO
        elif strength in ("strong", "moderate") and score >= 65:
            mode = ExecutionMode.ASSISTED
        else:
            mode = ExecutionMode.MANUAL

        price = data.get("recommended_price") or data.get("buy_price", 0)
        if not price or price <= 0:
            logger.warning("[ExecutionBridge] No valid price for opp #%s", data.get("opportunity_id"))
            return None

        try:
            order = await self._service.create_order(
                product_id=data["product_id"],
                order_type=OrderType.BUY,
                marketplace=data.get("buy_marketplace", ""),
                price=price,
                quantity=1,
                opportunity_id=data.get("opportunity_id"),
                estimated_profit=data.get("net_profit", 0),
                execution_mode=mode,
            )
            return order
        except Exception:
            logger.error(
                "[ExecutionBridge] Failed to create order for opp #%s",
                data.get("opportunity_id"), exc_info=True,
            )
            return None
