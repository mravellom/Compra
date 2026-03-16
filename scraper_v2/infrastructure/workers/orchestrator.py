"""Scraper Orchestrator — top-level cycle coordinator.

Implements ScraperOrchestratorPort. Runs continuous scraping cycles:
  1. Scheduler generates tasks
  2. ScrapingService runs workers to consume tasks
  3. Results published to Redis
  4. Metrics collected
  5. Wait for next cycle
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time

from scraper_v2.domain.enums import Marketplace
from scraper_v2.domain.models import ScraperMetrics
from scraper_v2.domain.ports import DedupPort, ScraperOrchestratorPort

from ...application.scheduler.request_scheduler import RequestScheduler
from ...application.services.scraping_service import ScrapingService

logger = logging.getLogger(__name__)


class ScraperOrchestrator(ScraperOrchestratorPort):
    """Runs scraping cycles in a loop with graceful shutdown."""

    def __init__(
        self,
        scheduler: RequestScheduler,
        scraping_service: ScrapingService,
        dedup: DedupPort,
        marketplaces: list[Marketplace],
        cycle_interval: float = 300.0,
    ) -> None:
        self._scheduler = scheduler
        self._service = scraping_service
        self._dedup = dedup
        self._marketplaces = marketplaces
        self._interval = cycle_interval
        self._running = False
        self._cycle_count = 0

    async def run_cycle(self) -> ScraperMetrics:
        """Execute a single scraping cycle."""
        self._cycle_count += 1
        cycle_start = time.monotonic()

        logger.info("═══ Cycle %d starting ═══", self._cycle_count)

        # Reset per-cycle dedup
        await self._dedup.reset()

        # Generate tasks
        task_count = await self._scheduler.generate_cycle(self._marketplaces)
        logger.info("Generated %d tasks for %d marketplaces", task_count, len(self._marketplaces))

        # Run workers
        metrics = await self._service.run_workers()

        elapsed = time.monotonic() - cycle_start
        logger.info(
            "═══ Cycle %d complete: %d listings in %.1fs (%.1f/s) | blocks=%d ═══",
            self._cycle_count,
            metrics.listings_total,
            elapsed,
            metrics.listings_per_second,
            metrics.blocks_detected,
        )

        return metrics

    async def run_forever(self) -> None:
        """Run cycles continuously until shutdown."""
        self._running = True
        loop = asyncio.get_event_loop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        logger.info(
            "Scraper orchestrator started: %d marketplaces, interval=%.0fs",
            len(self._marketplaces),
            self._interval,
        )

        while self._running:
            try:
                await self.run_cycle()
            except Exception as exc:
                logger.error("Cycle %d failed: %s", self._cycle_count, exc, exc_info=True)

            if self._running:
                logger.info("Sleeping %.0fs until next cycle...", self._interval)
                await asyncio.sleep(self._interval)

    async def shutdown(self) -> None:
        logger.info("Shutting down orchestrator...")
        self._running = False
        await self._service.shutdown()
