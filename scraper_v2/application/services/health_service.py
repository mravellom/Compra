"""Health Service — aggregates system health for observability."""

from __future__ import annotations

from dataclasses import dataclass

from scraper_v2.domain.ports import ProxyPoolPort

from ..resilience.circuit_breaker import CircuitBreaker
from ..resilience.bulkhead import Bulkhead
from ..scheduler.rate_limiter import AdaptiveRateLimiter


@dataclass(frozen=True, slots=True)
class SystemHealth:
    status: str  # "healthy" | "degraded" | "unhealthy"
    proxies: dict[str, object]
    circuits: dict[str, dict[str, object]]
    rate_limits: dict[str, dict[str, float]]
    bulkheads: dict[str, int]


class HealthService:
    """Aggregates health status from all subsystems."""

    def __init__(
        self,
        proxy_pool: ProxyPoolPort,
        circuit_breaker: CircuitBreaker,
        rate_limiter: AdaptiveRateLimiter,
        bulkhead: Bulkhead,
    ) -> None:
        self._proxy_pool = proxy_pool
        self._circuit = circuit_breaker
        self._rate_limiter = rate_limiter
        self._bulkhead = bulkhead

    async def check(self) -> SystemHealth:
        proxy_health = await self._proxy_pool.health_check()
        healthy_proxies = sum(1 for p in proxy_health if p.status.value == "healthy")
        total_proxies = len(proxy_health)

        circuits = self._circuit.snapshot()
        open_circuits = sum(1 for c in circuits.values() if c.get("state") == "open")

        status = "healthy"
        if open_circuits > 0 or (total_proxies > 0 and healthy_proxies < total_proxies * 0.5):
            status = "degraded"
        if open_circuits > len(circuits) * 0.5:
            status = "unhealthy"

        return SystemHealth(
            status=status,
            proxies={
                "healthy": healthy_proxies,
                "total": total_proxies,
                "details": [
                    {
                        "host": p.proxy.host,
                        "status": p.status.value,
                        "avg_latency_ms": round(p.avg_latency_ms, 1),
                        "failure_rate": round(p.failure_rate, 3),
                    }
                    for p in proxy_health
                ],
            },
            circuits=circuits,
            rate_limits=self._rate_limiter.snapshot(),
            bulkheads=self._bulkhead.snapshot(),
        )
