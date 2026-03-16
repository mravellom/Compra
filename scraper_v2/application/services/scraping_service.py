"""Scraping Service — the main application service that orchestrates the pipeline.

This is the entry point for the scraping workflow:
  1. Scheduler generates CrawlTasks
  2. Workers consume tasks from the queue
  3. Each task goes through: rate-limit → circuit-breaker → fetch → parse → dedup → publish
  4. Results flow into Redis Streams for the processor pipeline
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from scraper_v2.domain.enums import Marketplace
from scraper_v2.domain.events import (
    BackpressureEvent,
    BatchScrapedEvent,
    BlockDetectedEvent,
    CycleCompletedEvent,
)
from scraper_v2.domain.models import CrawlResult, CrawlTask, RawListing, ScraperMetrics
from scraper_v2.domain.ports import (
    BlockDetectorPort,
    BrowserClientPort,
    DedupPort,
    HttpClientPort,
    ListingParserPort,
    MetricsPort,
    ProxyPoolPort,
    StreamProducerPort,
    TaskQueuePort,
)

from ..resilience.bulkhead import Bulkhead
from ..resilience.circuit_breaker import CircuitBreaker
from ..resilience.retry import RetryConfig, RetryPolicy
from ..scheduler.rate_limiter import AdaptiveRateLimiter
from ..strategies.anti_blocking import DelayStrategy

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScrapingServiceConfig:
    worker_count: int = 10
    publish_batch_size: int = 100
    cycle_timeout: float = 600.0
    backpressure_check_interval: float = 30.0
    metrics_interval: float = 10.0


class ScrapingService:
    """Coordinates the full scraping pipeline with resilience patterns."""

    def __init__(
        self,
        *,
        task_queue: TaskQueuePort,
        http_client: HttpClientPort,
        browser_client: BrowserClientPort,
        parser: ListingParserPort,
        proxy_pool: ProxyPoolPort,
        block_detector: BlockDetectorPort,
        dedup: DedupPort,
        producer: StreamProducerPort,
        rate_limiter: AdaptiveRateLimiter,
        circuit_breaker: CircuitBreaker,
        bulkhead: Bulkhead,
        delay_strategy: DelayStrategy,
        retry_policy: RetryPolicy,
        metrics: MetricsPort | None = None,
        config: ScrapingServiceConfig | None = None,
    ) -> None:
        self._queue = task_queue
        self._http = http_client
        self._browser = browser_client
        self._parser = parser
        self._proxy_pool = proxy_pool
        self._block_detector = block_detector
        self._dedup = dedup
        self._producer = producer
        self._rate_limiter = rate_limiter
        self._circuit_breaker = circuit_breaker
        self._bulkhead = bulkhead
        self._delay = delay_strategy
        self._retry = retry_policy
        self._metrics = metrics
        self._config = config or ScrapingServiceConfig()

        self._running = False
        self._total_listings = 0
        self._total_blocks = 0
        self._start_time = 0.0
        self._publish_buffer: list[RawListing] = []

    async def run_workers(self) -> ScraperMetrics:
        """Start worker pool and process tasks until queue is empty."""
        self._running = True
        self._start_time = time.monotonic()
        self._total_listings = 0
        self._total_blocks = 0

        workers = [
            asyncio.create_task(
                self._worker_loop(f"worker-{i}"),
                name=f"scraper-worker-{i}",
            )
            for i in range(self._config.worker_count)
        ]

        monitor = asyncio.create_task(self._backpressure_monitor())

        try:
            await asyncio.gather(*workers)
        finally:
            self._running = False
            monitor.cancel()
            await self._flush_buffer()

        elapsed = time.monotonic() - self._start_time
        return ScraperMetrics(
            active_workers=0,
            idle_workers=self._config.worker_count,
            tasks_queued=0,
            tasks_completed=self._total_listings,
            listings_total=self._total_listings,
            listings_per_second=self._total_listings / elapsed if elapsed > 0 else 0,
            blocks_detected=self._total_blocks,
            proxies_healthy=self._proxy_pool.healthy_count(),
            proxies_total=self._proxy_pool.healthy_count(),
            circuit_breakers_open=self._circuit_breaker.open_count(),
            uptime_seconds=elapsed,
        )

    async def _worker_loop(self, worker_id: str) -> None:
        """Single worker: dequeue → execute → publish. Exits when queue empty."""
        logger.debug("Worker %s started", worker_id)
        consecutive_empty = 0

        while self._running:
            task = await self._queue.dequeue(timeout=2.0)
            if task is None:
                consecutive_empty += 1
                if consecutive_empty > 3:
                    break
                continue
            consecutive_empty = 0

            try:
                result = await self._execute_task(task)
                if result.success and result.listings:
                    await self._handle_result(result)
                elif result.blocked:
                    self._total_blocks += 1
            except Exception as exc:
                logger.error("Worker %s task %s failed: %s", worker_id, task.task_id, exc)

        logger.debug("Worker %s finished", worker_id)

    async def _execute_task(self, task: CrawlTask) -> CrawlResult:
        """Execute a single crawl task with full resilience stack."""
        mp = task.marketplace

        # Circuit breaker check
        if not await self._circuit_breaker.allow_request(mp):
            return CrawlResult(
                task=task, listings=[], blocked=True,
                error="Circuit breaker open",
            )

        # Bulkhead check
        if not await self._bulkhead.acquire(mp):
            await self._queue.enqueue(task)  # Re-queue
            return CrawlResult(task=task, listings=[], error="Bulkhead full")

        try:
            # Rate limit
            await self._rate_limiter.acquire(mp)

            # Delay jitter
            delay = self._delay.compute()
            await asyncio.sleep(delay)

            # Fetch
            start = time.monotonic()
            proxy = await self._proxy_pool.acquire(mp)

            if mp.requires_browser:
                html, _ = await self._browser.fetch_rendered(
                    task.url, marketplace=mp, scroll=True
                )
                status_code = 200
                resp_headers: dict[str, str] = {}
            else:
                status_code, html, resp_headers = await self._http.fetch(
                    task.url, marketplace=mp, proxy=proxy
                )

            duration_ms = (time.monotonic() - start) * 1000

            # Report proxy result
            if proxy:
                await self._proxy_pool.release(
                    proxy, success=(status_code == 200), latency_ms=duration_ms
                )

            # Block detection
            block = self._block_detector.detect(status_code, html, resp_headers)
            if block.blocked:
                await self._circuit_breaker.record_failure(mp)
                await self._rate_limiter.report_block(mp)
                if self._metrics:
                    self._metrics.increment("blocks", tags={"marketplace": mp.value, "type": block.block_type.value})
                return CrawlResult(
                    task=task, listings=[], blocked=True,
                    block_type=block.block_type, duration_ms=duration_ms,
                )

            # Parse
            listings = self._parser.parse(html, mp, task.url)
            next_page = self._parser.extract_next_page(html, mp)

            await self._circuit_breaker.record_success(mp)
            await self._rate_limiter.report_success(mp)

            if self._metrics:
                self._metrics.increment("listings_scraped", len(listings), {"marketplace": mp.value})
                self._metrics.timing("crawl_duration_ms", duration_ms, {"marketplace": mp.value})

            return CrawlResult(
                task=task,
                listings=listings,
                next_page_url=next_page,
                duration_ms=duration_ms,
            )

        except Exception as exc:
            await self._circuit_breaker.record_failure(mp)
            return CrawlResult(task=task, listings=[], error=str(exc))

        finally:
            self._bulkhead.release(mp)

    async def _handle_result(self, result: CrawlResult) -> None:
        """Dedup and buffer listings for batch publishing."""
        unique = await self._dedup.filter_new(result.listings)
        if not unique:
            return

        self._publish_buffer.extend(unique)
        self._total_listings += len(unique)

        if len(self._publish_buffer) >= self._config.publish_batch_size:
            await self._flush_buffer()

    async def _flush_buffer(self) -> None:
        """Publish buffered listings to Redis stream."""
        if not self._publish_buffer:
            return
        batch = self._publish_buffer[: self._config.publish_batch_size]
        self._publish_buffer = self._publish_buffer[self._config.publish_batch_size:]

        try:
            published = await self._producer.publish_batch(batch)
            if self._metrics:
                self._metrics.increment("listings_published", published)
        except Exception as exc:
            logger.error("Failed to publish batch of %d: %s", len(batch), exc)
            # Re-buffer on failure
            self._publish_buffer = batch + self._publish_buffer

    async def _backpressure_monitor(self) -> None:
        """Periodically check downstream backpressure."""
        while self._running:
            try:
                under_pressure = await self._producer.check_backpressure()
                if under_pressure:
                    logger.warning("Backpressure detected, throttling workers")
                    await asyncio.sleep(5.0)
                await asyncio.sleep(self._config.backpressure_check_interval)
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(5.0)

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        self._running = False
        await self._flush_buffer()
        await self._http.close()
        await self._browser.close()
        await self._producer.close()
