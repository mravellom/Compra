"""Redis Streams consumer for triggering orchestrator pipelines.

Listens to opportunity scan events and dispatches them to the orchestrator.
Designed to run as a background task within the FastAPI lifespan.
"""
import asyncio
import json
import logging
import os
from typing import Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

CONSUMER_GROUP = "orchestrator-group"
CONSUMER_NAME = os.getenv("ORCHESTRATOR_CONSUMER", "orchestrator-1")
LISTEN_STREAM = "orchestrator_input"     # Populated by the scan loop
BLOCK_MS = 5000
BATCH_SIZE = 10


class OrchestratorConsumer:
    """Consumes events from Redis Streams and dispatches to the orchestrator.

    Event contract (orchestrator_input stream):
    {
        "event_type": "opportunities_detected",
        "data": JSON string of {
            "opportunities": [
                {
                    "opportunity_id": int,
                    "product_id": int,
                    "buy_price": float,
                    "sell_price": float,
                    "net_profit": float,
                    "roi": float,
                    "buy_marketplace": str,
                    "sell_marketplace": str,
                    "opportunity_score": float,
                    "confidence_score": float,
                    "risk_score": float
                }, ...
            ]
        }
    }
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        orchestrator_service,
    ) -> None:
        self._redis = redis_client
        self._orchestrator = orchestrator_service
        self._running = False

    async def start(self) -> None:
        """Initialize consumer group and start processing loop."""
        # Create consumer group (ignore if exists)
        try:
            await self._redis.xgroup_create(
                LISTEN_STREAM, CONSUMER_GROUP, id="0", mkstream=True
            )
            logger.info("Created consumer group %s on %s", CONSUMER_GROUP, LISTEN_STREAM)
        except aioredis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
            logger.debug("Consumer group %s already exists", CONSUMER_GROUP)

        self._running = True
        logger.info("Orchestrator consumer started: %s", CONSUMER_NAME)

        while self._running:
            try:
                messages = await self._redis.xreadgroup(
                    CONSUMER_GROUP, CONSUMER_NAME,
                    {LISTEN_STREAM: ">"},
                    count=BATCH_SIZE,
                    block=BLOCK_MS,
                )

                if not messages:
                    continue

                for stream, entries in messages:
                    for msg_id, fields in entries:
                        await self._process_message(msg_id, fields)

            except asyncio.CancelledError:
                logger.info("Orchestrator consumer shutting down")
                break
            except Exception:
                logger.error("Consumer error, retrying in 5s", exc_info=True)
                await asyncio.sleep(5)

    async def stop(self) -> None:
        self._running = False

    async def _process_message(self, msg_id: str, fields: dict) -> None:
        """Process a single message from the stream."""
        try:
            event_type = fields.get("event_type", "")
            raw_data = fields.get("data", "{}")

            if isinstance(raw_data, bytes):
                raw_data = raw_data.decode()
            data = json.loads(raw_data)

            if event_type == "opportunities_detected":
                await self._handle_opportunities(data)
            else:
                logger.debug("Ignoring unknown event type: %s", event_type)

            # ACK the message
            await self._redis.xack(LISTEN_STREAM, CONSUMER_GROUP, msg_id)

        except Exception:
            logger.error("Failed to process message %s", msg_id, exc_info=True)
            # Don't ACK — message will be retried via pending

    async def _handle_opportunities(self, data: dict) -> None:
        """Dispatch opportunities to the orchestrator."""
        from ..domain.models import OpportunityContext

        opportunities_raw = data.get("opportunities", [])
        if not opportunities_raw:
            return

        contexts = []
        for opp in opportunities_raw:
            try:
                ctx = OpportunityContext(**opp)
                contexts.append(ctx)
            except Exception:
                logger.warning("Invalid opportunity data: %s", opp, exc_info=True)

        if contexts:
            logger.info("Dispatching %d opportunities to orchestrator", len(contexts))
            await self._orchestrator.process_batch(contexts)
