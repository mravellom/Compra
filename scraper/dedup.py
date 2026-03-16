"""
Deduplication module for scraped listings.

Uses a hash of (title_normalized + marketplace + price) to detect duplicates
within a scraping run. Optionally backed by Redis for cross-run dedup.
"""
import hashlib
import logging
import re

import redis.asyncio as redis

from .schemas import RawListing

logger = logging.getLogger(__name__)

# Redis key for the dedup set, with a TTL so stale hashes expire
DEDUP_KEY = "scraper:dedup:hashes"
DEDUP_TTL_SECONDS = 86400  # 24 hours


def _normalize_title(title: str) -> str:
    """Normalize title for dedup: lowercase, strip whitespace, remove special chars."""
    text = title.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def listing_hash(listing: RawListing) -> str:
    """Compute a dedup hash for a listing."""
    key = f"{_normalize_title(listing.title)}|{listing.marketplace_id}|{listing.price:.2f}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


class DedupFilter:
    """
    In-memory + optional Redis dedup filter.

    Usage:
        dedup = DedupFilter(redis_client)
        unique = await dedup.filter_batch(listings)
    """

    def __init__(self, redis_client: redis.Redis | None = None):
        self._redis = redis_client
        self._local: set[str] = set()

    async def filter_batch(self, listings: list[RawListing]) -> list[RawListing]:
        """Return only listings not seen before in this run or in Redis."""
        unique: list[RawListing] = []
        new_hashes: list[str] = []

        for listing in listings:
            h = listing_hash(listing)

            # Check local set first (fast)
            if h in self._local:
                continue

            self._local.add(h)
            new_hashes.append(h)
            unique.append(listing)

        # Check and add to Redis in a single pipeline (1 RTT instead of 2)
        if self._redis and new_hashes:
            try:
                pipe = self._redis.pipeline()
                # Interleave SISMEMBER + SADD in one pipeline
                for h in new_hashes:
                    pipe.sismember(DEDUP_KEY, h)
                    pipe.sadd(DEDUP_KEY, h)
                pipe.expire(DEDUP_KEY, DEDUP_TTL_SECONDS)
                results = await pipe.execute()

                # Results alternate: [sismember_0, sadd_0, sismember_1, sadd_1, ..., expire]
                filtered = []
                for i, (listing, h) in enumerate(zip(unique, new_hashes)):
                    already_seen = results[i * 2]  # sismember result
                    if not already_seen:
                        filtered.append(listing)

                unique = filtered
            except Exception:
                logger.warning("Redis dedup check failed, using local only", exc_info=True)

        return unique

    @property
    def local_count(self) -> int:
        """Number of unique hashes seen in this run."""
        return len(self._local)

    def reset(self) -> None:
        """Clear local dedup set (for new run)."""
        self._local.clear()
