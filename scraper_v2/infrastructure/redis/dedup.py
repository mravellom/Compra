"""Redis-backed deduplication with local LRU cache."""

from __future__ import annotations

import logging
from collections import OrderedDict

import redis.asyncio as aioredis

from scraper_v2.domain.models import RawListing
from scraper_v2.domain.ports import DedupPort

logger = logging.getLogger(__name__)


class RedisDedup(DedupPort):
    """Deduplication using Redis SET with local LRU pre-filter."""

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        redis_key: str = "scraper_v2:dedup",
        ttl_seconds: int = 86400,
        local_cache_size: int = 50_000,
    ) -> None:
        self._redis_url = redis_url
        self._redis_key = redis_key
        self._ttl = ttl_seconds
        self._local: OrderedDict[str, None] = OrderedDict()
        self._local_max = local_cache_size
        self._redis: aioredis.Redis | None = None

    async def _ensure(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def filter_new(self, listings: list[RawListing]) -> list[RawListing]:
        """Return only listings not seen in local cache or Redis."""
        if not listings:
            return []

        # Local pre-filter
        candidates: list[tuple[RawListing, str]] = []
        for l in listings:
            key = l.dedup_key
            if key in self._local:
                continue
            candidates.append((l, key))

        if not candidates:
            return []

        # Redis batch check
        r = await self._ensure()
        keys = [k for _, k in candidates]

        try:
            async with r.pipeline(transaction=False) as pipe:
                for k in keys:
                    pipe.sismember(self._redis_key, k)
                results = await pipe.execute()
        except Exception as exc:
            logger.warning("Redis dedup check failed, passing all: %s", exc)
            results = [False] * len(keys)

        new_listings: list[RawListing] = []
        new_keys: list[str] = []

        for (listing, key), exists in zip(candidates, results):
            if not exists:
                new_listings.append(listing)
                new_keys.append(key)
                self._local[key] = None
                if len(self._local) > self._local_max:
                    self._local.popitem(last=False)

        # Register new keys in Redis
        if new_keys:
            try:
                async with r.pipeline(transaction=False) as pipe:
                    for k in new_keys:
                        pipe.sadd(self._redis_key, k)
                    pipe.expire(self._redis_key, self._ttl)
                    await pipe.execute()
            except Exception as exc:
                logger.warning("Redis dedup register failed: %s", exc)

        return new_listings

    async def reset(self) -> None:
        self._local.clear()
        try:
            r = await self._ensure()
            await r.delete(self._redis_key)
        except Exception:
            pass
