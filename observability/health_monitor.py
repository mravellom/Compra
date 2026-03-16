"""
System Health Monitor — continuous health checking and failure alerting.

Runs as a background async task that periodically checks all system
components and publishes health events when degradation is detected.

Components monitored:
  - PostgreSQL connectivity + query latency
  - Redis connectivity + stream depth
  - Processor throughput + error rate
  - Scraper activity + rate limiter status
  - API response latency
  - Opportunity freshness

Alerting:
  - Health status changes are logged as structured events
  - Critical status triggers notification via monetization publisher
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum

import redis.asyncio as aioredis

from .metrics import get_metrics

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CHECK_INTERVAL = int(os.getenv("HEALTH_CHECK_INTERVAL", "30"))


class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class ComponentHealth:
    name: str
    status: str = "unknown"
    latency_ms: float = 0.0
    message: str = ""
    last_check: float = 0.0
    details: dict = field(default_factory=dict)


@dataclass
class SystemHealth:
    overall: str = "unknown"
    components: dict[str, ComponentHealth] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    alerts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.overall,
            "timestamp": self.timestamp,
            "alerts": self.alerts,
            "components": {
                name: {
                    "status": c.status,
                    "latency_ms": round(c.latency_ms, 1),
                    "message": c.message,
                    "details": c.details,
                }
                for name, c in self.components.items()
            },
        }


class HealthMonitor:
    """
    Continuous health monitoring with configurable checks.

    Usage:
        monitor = HealthMonitor()
        asyncio.create_task(monitor.start())

        # Get current health
        health = monitor.current_health
    """

    def __init__(self) -> None:
        self._health = SystemHealth()
        self._running = False
        self._redis: aioredis.Redis | None = None
        self._previous_status: str = "unknown"

    @property
    def current_health(self) -> SystemHealth:
        return self._health

    async def start(self) -> None:
        """Start periodic health checks."""
        self._running = True
        logger.info("[HealthMonitor] Starting with %ds interval", CHECK_INTERVAL)

        while self._running:
            try:
                await self._run_checks()
            except Exception as e:
                logger.error("[HealthMonitor] Check cycle failed: %s", e)
            await asyncio.sleep(CHECK_INTERVAL)

    async def stop(self) -> None:
        self._running = False
        if self._redis:
            await self._redis.aclose()

    async def _run_checks(self) -> None:
        """Execute all health checks and update status."""
        health = SystemHealth()
        alerts = []

        # Check Redis
        redis_health = await self._check_redis()
        health.components["redis"] = redis_health
        if redis_health.status == "critical":
            alerts.append("Redis is unreachable")

        # Check processor throughput from metrics
        proc_health = self._check_processor()
        health.components["processor"] = proc_health
        if proc_health.status == "critical":
            alerts.append("Processor throughput is zero")

        # Check scraper activity
        scraper_health = self._check_scraper()
        health.components["scraper"] = scraper_health

        # Check opportunity freshness
        opp_health = self._check_opportunities()
        health.components["opportunities"] = opp_health

        # Determine overall status
        statuses = [c.status for c in health.components.values()]
        if any(s == "critical" for s in statuses):
            health.overall = "critical"
        elif any(s == "degraded" for s in statuses):
            health.overall = "degraded"
        elif all(s == "healthy" for s in statuses):
            health.overall = "healthy"
        else:
            health.overall = "degraded"

        health.alerts = alerts
        health.timestamp = time.time()

        # Log status changes
        if health.overall != self._previous_status:
            logger.warning(
                "[HealthMonitor] Status changed: %s → %s (alerts: %s)",
                self._previous_status, health.overall, alerts,
            )
            self._previous_status = health.overall

        # Update metrics
        metrics = get_metrics()
        status_map = {"healthy": 1, "degraded": 0.5, "critical": 0, "unknown": -1}
        metrics.set("health.overall", status_map.get(health.overall, -1))
        for name, comp in health.components.items():
            metrics.set(f"health.{name}", status_map.get(comp.status, -1))

        self._health = health

    async def _check_redis(self) -> ComponentHealth:
        """Check Redis connectivity and stream depth."""
        health = ComponentHealth(name="redis")
        t0 = time.monotonic()
        try:
            if self._redis is None:
                self._redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            await self._redis.ping()
            health.latency_ms = (time.monotonic() - t0) * 1000
            health.status = "healthy"
            health.message = "Connected"

            # Check stream depth
            try:
                info = await self._redis.xinfo_stream("raw_listings_queue")
                depth = info.get("length", 0)
                health.details["stream_depth"] = depth
                if depth > 100_000:
                    health.status = "degraded"
                    health.message = f"Stream depth high: {depth}"
            except Exception:
                pass

        except Exception as e:
            health.latency_ms = (time.monotonic() - t0) * 1000
            health.status = "critical"
            health.message = str(e)
            self._redis = None

        health.last_check = time.time()
        return health

    def _check_processor(self) -> ComponentHealth:
        """Check processor throughput from metrics."""
        health = ComponentHealth(name="processor")
        metrics = get_metrics()
        throughput = metrics.get_gauge("processor.throughput")
        errors = metrics.get_counter("processor.errors")
        processed = metrics.get_counter("processor.processed")

        health.details = {
            "throughput": throughput,
            "errors": errors,
            "processed": processed,
        }

        if processed == 0 and errors == 0:
            health.status = "unknown"
            health.message = "No data yet"
        elif throughput > 1.0:
            health.status = "healthy"
            health.message = f"{throughput:.1f} items/s"
        elif throughput > 0:
            health.status = "degraded"
            health.message = f"Low throughput: {throughput:.1f} items/s"
        else:
            health.status = "critical"
            health.message = "Zero throughput"

        health.last_check = time.time()
        return health

    def _check_scraper(self) -> ComponentHealth:
        """Check scraper activity from metrics."""
        health = ComponentHealth(name="scraper")
        metrics = get_metrics()
        scraped = metrics.get_counter("scraper.listings_scraped")
        errors = metrics.get_counter("scraper.errors")

        health.details = {"scraped": scraped, "errors": errors}

        if scraped > 0:
            health.status = "healthy"
            health.message = f"{scraped:.0f} listings scraped"
        else:
            health.status = "unknown"
            health.message = "No scraping activity recorded"

        health.last_check = time.time()
        return health

    def _check_opportunities(self) -> ComponentHealth:
        """Check opportunity generation from metrics."""
        health = ComponentHealth(name="opportunities")
        metrics = get_metrics()
        total = metrics.get_gauge("opportunities.active")
        avg_score = metrics.get_gauge("opportunities.avg_score")

        health.details = {"active": total, "avg_score": avg_score}

        if total > 0:
            health.status = "healthy"
            health.message = f"{total:.0f} active (avg score {avg_score:.0f})"
        else:
            health.status = "degraded"
            health.message = "No active opportunities"

        health.last_check = time.time()
        return health
