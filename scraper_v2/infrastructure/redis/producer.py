"""Redis Stream Producer — publishes listings with backpressure awareness.

Implements StreamProducerPort with:
- Pipeline batching for efficient Redis writes
- Backpressure detection via stream length monitoring
- Idempotent publishing (dedup key as message field)
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

from scraper_v2.domain.models import RawListing
from scraper_v2.domain.ports import StreamProducerPort

logger = logging.getLogger(__name__)


class RedisStreamProducer(StreamProducerPort):
    """Publishes listing batches to Redis Streams with backpressure."""

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        stream_key: str = "raw_listings_queue",
        max_stream_length: int = 100_000,
        backpressure_threshold: int = 50_000,
        batch_pipeline_size: int = 100,
    ) -> None:
        self._redis_url = redis_url
        self._stream_key = stream_key
        self._max_len = max_stream_length
        self._bp_threshold = backpressure_threshold
        self._pipeline_size = batch_pipeline_size
        self._redis: aioredis.Redis | None = None

    async def _ensure_connection(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                max_connections=10,
            )
        return self._redis

    async def publish_batch(self, listings: list[RawListing]) -> int:
        """Publish listings in pipeline batches. Returns published count."""
        if not listings:
            return 0

        r = await self._ensure_connection()
        published = 0

        for i in range(0, len(listings), self._pipeline_size):
            chunk = listings[i: i + self._pipeline_size]
            async with r.pipeline(transaction=False) as pipe:
                for listing in chunk:
                    data = listing.to_stream_dict()
                    data["_dedup_key"] = listing.dedup_key
                    pipe.xadd(
                        self._stream_key,
                        data,
                        maxlen=self._max_len,
                        approximate=True,
                    )
                results = await pipe.execute()
                published += sum(1 for r in results if r)

        return published

    async def check_backpressure(self) -> bool:
        """Check if downstream processing is falling behind."""
        r = await self._ensure_connection()
        try:
            length = await r.xlen(self._stream_key)
            if length > self._bp_threshold:
                logger.warning(
                    "Backpressure: stream length %d > threshold %d",
                    length,
                    self._bp_threshold,
                )
                return True
            return False
        except Exception as exc:
            logger.error("Backpressure check failed: %s", exc)
            return False

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None
