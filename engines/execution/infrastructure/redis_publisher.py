"""Redis Streams publisher for execution events."""
import json
import logging
from typing import Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

STREAM_NAME = "execution_events"


class ExecutionEventPublisher:
    """Publishes execution events to Redis Streams."""

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client

    async def publish(self, event_type: str, data: dict) -> Optional[str]:
        try:
            message = {
                "event_type": event_type,
                "data": json.dumps(data),
            }
            msg_id = await self._redis.xadd(STREAM_NAME, message)
            logger.debug("Published %s to %s: %s", event_type, STREAM_NAME, msg_id)
            return msg_id
        except Exception:
            logger.error("Failed to publish %s", event_type, exc_info=True)
            return None
