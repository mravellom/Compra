"""Proxy Pool — manages proxy rotation, health tracking, and geo-distribution.

Implements ProxyPoolPort with:
- Round-robin rotation with health-aware selection
- Automatic cooldown for failing proxies
- Geo-affinity matching (LATAM proxies for ML, US for Amazon/eBay)
- Background health checking
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from scraper_v2.domain.enums import Marketplace, ProxyStatus
from scraper_v2.domain.models import ProxyEndpoint, ProxyHealth
from scraper_v2.domain.ports import ProxyPoolPort

logger = logging.getLogger(__name__)

# Geo-affinity: which regions work best for which marketplaces
_GEO_AFFINITY: dict[Marketplace, list[str]] = {
    Marketplace.MERCADOLIBRE_AR: ["ar", "latam", "us"],
    Marketplace.MERCADOLIBRE_MX: ["mx", "latam", "us"],
    Marketplace.MERCADOLIBRE_CL: ["cl", "latam", "us"],
    Marketplace.MERCADOLIBRE_CO: ["co", "latam", "us"],
    Marketplace.AMAZON_MX: ["mx", "us", "latam"],
    Marketplace.AMAZON_US: ["us"],
    Marketplace.EBAY: ["us", "eu"],
    Marketplace.ALIEXPRESS: ["us", "eu", "unknown"],
}


@dataclass(frozen=True, slots=True)
class ProxyPoolConfig:
    max_failures: int = 3
    cooldown_seconds: float = 300.0
    health_check_interval: float = 120.0
    health_check_url: str = "https://httpbin.org/ip"
    health_check_timeout: float = 10.0


class ProxyPool(ProxyPoolPort):
    """Production proxy pool with rotation, health, and geo-affinity."""

    def __init__(
        self,
        proxies: list[ProxyEndpoint],
        config: ProxyPoolConfig | None = None,
    ) -> None:
        self._config = config or ProxyPoolConfig()
        self._health: dict[str, ProxyHealth] = {
            p.url: ProxyHealth(proxy=p) for p in proxies
        }
        self._rotation_index = 0
        self._lock = asyncio.Lock()

    @classmethod
    def from_urls(cls, urls: list[str], config: ProxyPoolConfig | None = None) -> ProxyPool:
        """Create pool from URL strings."""
        proxies = []
        for url in urls:
            region = "unknown"
            # Detect region from URL hints
            lower = url.lower()
            for r in ["us", "mx", "ar", "cl", "co", "eu", "latam"]:
                if r in lower:
                    region = r
                    break
            proxies.append(ProxyEndpoint(url=url, region=region))
        return cls(proxies, config)

    async def acquire(self, marketplace: Marketplace | None = None) -> ProxyEndpoint | None:
        """Get next healthy proxy with optional geo-affinity."""
        async with self._lock:
            candidates = self._get_candidates(marketplace)
            if not candidates:
                # Fallback to any healthy proxy
                candidates = [
                    h for h in self._health.values()
                    if self._is_available(h)
                ]
            if not candidates:
                return None

            # Sort by latency (prefer faster proxies)
            candidates.sort(key=lambda h: h.avg_latency_ms)

            # Round-robin among top candidates
            idx = self._rotation_index % len(candidates)
            self._rotation_index += 1

            selected = candidates[idx]
            selected.last_used = time.monotonic()
            return selected.proxy

    async def release(
        self, proxy: ProxyEndpoint, success: bool, latency_ms: float = 0.0
    ) -> None:
        async with self._lock:
            health = self._health.get(proxy.url)
            if not health:
                return
            if success:
                health.record_success(latency_ms)
            else:
                health.record_failure(
                    self._config.max_failures,
                    self._config.cooldown_seconds,
                )

    async def health_check(self) -> list[ProxyHealth]:
        """Run health checks on all proxies."""
        tasks = [self._check_one(h) for h in self._health.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        return list(self._health.values())

    def healthy_count(self) -> int:
        return sum(1 for h in self._health.values() if self._is_available(h))

    def _get_candidates(self, marketplace: Marketplace | None) -> list[ProxyHealth]:
        if not marketplace:
            return [h for h in self._health.values() if self._is_available(h)]

        preferred_regions = _GEO_AFFINITY.get(marketplace, [])
        candidates = []
        for h in self._health.values():
            if not self._is_available(h):
                continue
            if h.proxy.region in preferred_regions:
                candidates.append(h)
        return candidates

    def _is_available(self, health: ProxyHealth) -> bool:
        if health.status == ProxyStatus.DEAD:
            return False
        if health.status == ProxyStatus.COOLDOWN:
            if time.monotonic() < health.cooldown_until:
                return False
            health.status = ProxyStatus.HEALTHY
            health.failure_count = 0
        return True

    async def _check_one(self, health: ProxyHealth) -> None:
        try:
            async with httpx.AsyncClient(
                proxy=health.proxy.url,
                timeout=self._config.health_check_timeout,
            ) as client:
                start = time.monotonic()
                resp = await client.get(self._config.health_check_url)
                latency = (time.monotonic() - start) * 1000

                if resp.status_code == 200:
                    health.record_success(latency)
                else:
                    health.record_failure(self._config.max_failures, self._config.cooldown_seconds)
        except Exception:
            health.record_failure(self._config.max_failures, self._config.cooldown_seconds)
