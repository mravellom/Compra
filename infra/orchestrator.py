"""
Scraping Orchestrator — generates scrape jobs and manages fleet.

Responsibilities:
1. Read marketplace configs
2. Generate (marketplace, category, page) jobs into scrape_jobs stream
3. Monitor backpressure (pause if downstream is overwhelmed)
4. Adaptive scheduling (prioritize hot categories)
5. Track job completion rates

Usage:
    python -m infra.orchestrator
"""
import asyncio
import logging
import os
import signal
import time
from dataclasses import dataclass

import redis.asyncio as redis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SCRAPE_JOBS_STREAM = "scrape_jobs"
RAW_LISTINGS_STREAM = "raw_listings"

# Backpressure thresholds
MAX_RAW_QUEUE = int(os.getenv("MAX_RAW_QUEUE", "50000"))
PAUSE_DURATION = int(os.getenv("PAUSE_DURATION", "60"))

# Scheduling
CYCLE_INTERVAL = int(os.getenv("CYCLE_INTERVAL", "1800"))  # 30 min default


@dataclass
class MarketplaceConfig:
    marketplace_id: str
    categories: list[str]
    pages_per_category: int
    rate_limit: float  # req/sec
    priority: str  # "high", "medium", "low"
    enabled: bool = True


# Default config — extend via YAML/DB in production
DEFAULT_CONFIGS = [
    MarketplaceConfig(
        marketplace_id="mercadolibre_ar",
        categories=os.getenv(
            "CATEGORIES", "electronica,videojuegos,celulares"
        ).split(","),
        pages_per_category=int(os.getenv("CATEGORY_MAX_PAGES", "3")),
        rate_limit=0.5,
        priority="high",
    ),
    MarketplaceConfig(
        marketplace_id="mercadolibre_mx",
        categories=os.getenv(
            "CATEGORIES", "electronica,videojuegos,celulares"
        ).split(","),
        pages_per_category=int(os.getenv("CATEGORY_MAX_PAGES", "3")),
        rate_limit=0.5,
        priority="high",
    ),
    MarketplaceConfig(
        marketplace_id="amazon",
        categories=os.getenv(
            "CATEGORIES", "electronica,videojuegos,celulares"
        ).split(","),
        pages_per_category=int(os.getenv("CATEGORY_MAX_PAGES", "3")),
        rate_limit=0.4,
        priority="medium",
    ),
]


class ScrapingOrchestrator:
    """Generates scrape jobs and manages backpressure."""

    def __init__(
        self,
        configs: list[MarketplaceConfig] | None = None,
        redis_url: str | None = None,
    ):
        self._configs = configs or DEFAULT_CONFIGS
        self._redis_url = redis_url or REDIS_URL
        self._redis: redis.Redis | None = None
        self._stop = asyncio.Event()
        self._cycle_count = 0
        self._total_jobs = 0

    async def run(self) -> None:
        """Main orchestrator loop."""
        self._redis = redis.from_url(self._redis_url, decode_responses=True)

        try:
            await self._redis.ping()
            logger.info("Orchestrator connected to Redis")
        except redis.ConnectionError:
            logger.error("Cannot connect to Redis")
            return

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._stop.set)

        enabled = [c for c in self._configs if c.enabled]
        logger.info(
            "Orchestrator started: %d marketplaces, %d total categories",
            len(enabled),
            sum(len(c.categories) for c in enabled),
        )

        try:
            while not self._stop.is_set():
                # Check backpressure before generating jobs
                if await self._should_pause():
                    logger.warning(
                        "Backpressure: raw_listings queue > %d, pausing %ds",
                        MAX_RAW_QUEUE, PAUSE_DURATION,
                    )
                    try:
                        await asyncio.wait_for(
                            self._stop.wait(), timeout=PAUSE_DURATION,
                        )
                    except asyncio.TimeoutError:
                        pass
                    continue

                # Generate jobs for this cycle
                jobs_created = await self._generate_cycle()
                self._cycle_count += 1
                self._total_jobs += jobs_created

                logger.info(
                    "Cycle %d: %d jobs created (total: %d)",
                    self._cycle_count, jobs_created, self._total_jobs,
                )

                # Wait for next cycle
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=CYCLE_INTERVAL,
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            await self._redis.aclose()
            logger.info("Orchestrator shutdown")

    async def _should_pause(self) -> bool:
        """Check if downstream queues are overwhelmed."""
        try:
            length = await self._redis.xlen(RAW_LISTINGS_STREAM)
            return length > MAX_RAW_QUEUE
        except Exception:
            return False

    async def _generate_cycle(self) -> int:
        """Generate scrape jobs for all enabled marketplaces."""
        jobs = 0
        priority_order = {"high": 0, "medium": 1, "low": 2}
        sorted_configs = sorted(
            [c for c in self._configs if c.enabled],
            key=lambda c: priority_order.get(c.priority, 1),
        )

        for config in sorted_configs:
            for category in config.categories:
                category = category.strip()
                if not category:
                    continue
                for page in range(1, config.pages_per_category + 1):
                    await self._redis.xadd(SCRAPE_JOBS_STREAM, {
                        "marketplace": config.marketplace_id,
                        "category": category,
                        "page": str(page),
                        "priority": config.priority,
                        "rate_limit": str(config.rate_limit),
                        "created_at": str(time.time()),
                    })
                    jobs += 1

        return jobs

    async def get_status(self) -> dict:
        """Return orchestrator status for monitoring."""
        raw_len = await self._redis.xlen(RAW_LISTINGS_STREAM) if self._redis else 0
        jobs_len = await self._redis.xlen(SCRAPE_JOBS_STREAM) if self._redis else 0
        return {
            "cycles_completed": self._cycle_count,
            "total_jobs_created": self._total_jobs,
            "pending_scrape_jobs": jobs_len,
            "raw_listings_queue": raw_len,
            "backpressure_active": raw_len > MAX_RAW_QUEUE,
        }


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    orchestrator = ScrapingOrchestrator()
    await orchestrator.run()


if __name__ == "__main__":
    asyncio.run(main())
