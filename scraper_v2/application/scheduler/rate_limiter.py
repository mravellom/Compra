"""Adaptive rate limiter — per-marketplace token bucket with dynamic adjustment.

The limiter monitors block rates and automatically reduces throughput when
a marketplace starts blocking, then gradually increases when blocks stop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from scraper_v2.domain.enums import Marketplace

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    default_rps: float = 2.0
    min_rps: float = 0.1
    max_rps: float = 10.0
    burst: int = 3
    backoff_factor: float = 0.5
    recovery_factor: float = 1.1
    block_window: float = 300.0  # 5 min window for block rate


@dataclass
class _TokenBucket:
    rate: float
    burst: int
    tokens: float = 0.0
    last_refill: float = field(default_factory=time.monotonic)
    blocks_in_window: int = 0
    requests_in_window: int = 0
    window_start: float = field(default_factory=time.monotonic)

    def refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.last_refill = now

    @property
    def block_rate(self) -> float:
        return self.blocks_in_window / self.requests_in_window if self.requests_in_window > 0 else 0.0


class AdaptiveRateLimiter:
    """Per-marketplace adaptive rate limiter."""

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self._config = config or RateLimitConfig()
        self._buckets: dict[Marketplace, _TokenBucket] = {}
        self._lock = asyncio.Lock()

    def _get_bucket(self, marketplace: Marketplace) -> _TokenBucket:
        if marketplace not in self._buckets:
            self._buckets[marketplace] = _TokenBucket(
                rate=self._config.default_rps,
                burst=self._config.burst,
                tokens=float(self._config.burst),
            )
        return self._buckets[marketplace]

    async def acquire(self, marketplace: Marketplace) -> float:
        """Wait until a token is available. Returns wait time in seconds."""
        async with self._lock:
            bucket = self._get_bucket(marketplace)
            bucket.refill()

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                bucket.requests_in_window += 1
                return 0.0

            wait = (1.0 - bucket.tokens) / bucket.rate
            bucket.tokens = 0.0
            bucket.requests_in_window += 1

        await asyncio.sleep(wait)
        return wait

    async def report_block(self, marketplace: Marketplace) -> None:
        """Report a block, triggering rate reduction."""
        async with self._lock:
            bucket = self._get_bucket(marketplace)
            self._reset_window_if_needed(bucket)
            bucket.blocks_in_window += 1

            if bucket.block_rate > 0.3:
                old_rate = bucket.rate
                bucket.rate = max(
                    self._config.min_rps,
                    bucket.rate * self._config.backoff_factor,
                )
                if bucket.rate != old_rate:
                    logger.warning(
                        "Rate limit %s: %.2f → %.2f rps (block rate %.0f%%)",
                        marketplace.value,
                        old_rate,
                        bucket.rate,
                        bucket.block_rate * 100,
                    )

    async def report_success(self, marketplace: Marketplace) -> None:
        """Report success, allowing gradual rate recovery."""
        async with self._lock:
            bucket = self._get_bucket(marketplace)
            self._reset_window_if_needed(bucket)

            if bucket.block_rate < 0.05 and bucket.requests_in_window > 20:
                old_rate = bucket.rate
                bucket.rate = min(
                    self._config.max_rps,
                    bucket.rate * self._config.recovery_factor,
                )
                if bucket.rate != old_rate:
                    logger.debug(
                        "Rate recovery %s: %.2f → %.2f rps",
                        marketplace.value,
                        old_rate,
                        bucket.rate,
                    )

    def _reset_window_if_needed(self, bucket: _TokenBucket) -> None:
        now = time.monotonic()
        if now - bucket.window_start > self._config.block_window:
            bucket.blocks_in_window = 0
            bucket.requests_in_window = 0
            bucket.window_start = now

    def get_rate(self, marketplace: Marketplace) -> float:
        return self._get_bucket(marketplace).rate

    def snapshot(self) -> dict[str, dict[str, float]]:
        return {
            mp.value: {
                "rate_rps": b.rate,
                "block_rate": b.block_rate,
                "tokens": b.tokens,
            }
            for mp, b in self._buckets.items()
        }
