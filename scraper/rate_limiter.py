"""
Async rate limiter and retry logic for scrapers.
Token-bucket algorithm with per-domain tracking.
"""
import asyncio
import logging
import time
from collections import defaultdict
from functools import wraps
from typing import TypeVar, Callable, Awaitable

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RateLimiter:
    """Token-bucket rate limiter scoped per domain."""

    def __init__(self, requests_per_second: float = 1.0, burst: int = 3):
        self._rate = requests_per_second
        self._burst = burst
        self._tokens: dict[str, float] = defaultdict(lambda: float(burst))
        self._last_refill: dict[str, float] = defaultdict(time.monotonic)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, domain: str = "default") -> None:
        async with self._locks[domain]:
            now = time.monotonic()
            elapsed = now - self._last_refill[domain]
            self._tokens[domain] = min(
                self._burst, self._tokens[domain] + elapsed * self._rate
            )
            self._last_refill[domain] = now

            if self._tokens[domain] < 1.0:
                wait = (1.0 - self._tokens[domain]) / self._rate
                logger.debug("Rate limit: waiting %.2fs for %s", wait, domain)
                await asyncio.sleep(wait)
                self._tokens[domain] = 0.0
            else:
                self._tokens[domain] -= 1.0


async def fetch_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    rate_limiter: RateLimiter | None = None,
    domain: str = "default",
    max_retries: int = 3,
    base_delay: float = 2.0,
    timeout: float = 30.0,
) -> httpx.Response:
    """GET with exponential backoff retry and optional rate limiting."""
    if rate_limiter:
        await rate_limiter.acquire(domain)

    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = await client.get(url, timeout=timeout)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", base_delay * (attempt + 1)))
                logger.warning("429 on %s, retry after %.1fs", domain, retry_after)
                await asyncio.sleep(retry_after)
                continue
            resp.raise_for_status()
            return resp
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout) as exc:
            last_exc = exc
            delay = base_delay * (2 ** attempt)
            logger.warning(
                "Attempt %d/%d failed for %s: %s. Retrying in %.1fs",
                attempt + 1, max_retries, url[:80], exc, delay,
            )
            await asyncio.sleep(delay)

    raise last_exc or RuntimeError(f"Failed to fetch {url} after {max_retries} retries")
