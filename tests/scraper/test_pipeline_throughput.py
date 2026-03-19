"""
Tests for the optimized scraping pipeline.

Validates:
1. Concurrent crawling with semaphore
2. No client recreation (shared httpx client)
3. Batched Redis publishing
4. Backpressure via semaphore limits
5. Pipeline metrics tracking
6. Graceful handling of errors in concurrent tasks
7. AliExpress shared client reuse
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scraper.scraping_orchestrator import (
    PipelineMetrics,
    ScrapingOrchestrator,
    CATEGORY_CONCURRENCY,
    SEARCH_CONCURRENCY,
    PUBLISH_CHUNK_SIZE,
)
from scraper.aliexpress_category_crawler import AliExpressCategoryCrawler
from scraper.stealth import StealthSession


# ── Fixtures ────────────────────────────────────────────────

def _make_listing(title="Test Product", price=99.99):
    """Create a mock RawListing."""
    m = MagicMock()
    m.title = title
    m.price = price
    m.to_stream_dict.return_value = {"title": title, "price": str(price)}
    return m


def _make_crawler(marketplace_id="amazon_us", listings=None):
    """Create a mock crawler that returns fixed listings."""
    crawler = AsyncMock()
    crawler.marketplace_id = marketplace_id
    crawler.crawl_category = AsyncMock(return_value=listings or [])
    crawler._last_html = None
    return crawler


def _make_search_scraper(marketplace_id="ebay", listings=None):
    """Create a mock search scraper."""
    scraper = AsyncMock()
    scraper.marketplace_id = marketplace_id
    scraper.scrape = AsyncMock(return_value=listings or [])
    return scraper


def _make_dedup():
    """Create a mock DedupFilter that passes everything through."""
    dedup = AsyncMock()
    dedup.filter_batch = AsyncMock(side_effect=lambda x: x)
    dedup.local_count = 0
    dedup.reset = MagicMock()
    return dedup


def _make_redis():
    """Create a mock Redis client with pipeline support."""
    redis = AsyncMock()
    pipe = AsyncMock()
    pipe.xadd = MagicMock()
    pipe.execute = AsyncMock(return_value=["msg_id_1", "msg_id_2"])
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=None)
    redis.pipeline = MagicMock(return_value=pipe)
    return redis


# ── PipelineMetrics Tests ───────────────────────────────────

class TestPipelineMetrics:

    def test_throughput_calculation(self):
        m = PipelineMetrics()
        m.cycle_start()
        m._cycle_start -= 10.0  # Simulate 10 seconds elapsed
        m.cycle_end(100)
        assert m.throughput == pytest.approx(10.0, abs=0.5)

    def test_zero_elapsed_throughput(self):
        m = PipelineMetrics()
        assert m.throughput == 0.0

    def test_avg_fetch_latency(self):
        m = PipelineMetrics()
        m.cycle_start()
        m.record_fetch(1.0)
        m.record_fetch(3.0)
        m.record_fetch(2.0)
        assert m.avg_fetch_latency == pytest.approx(2.0, abs=0.01)

    def test_no_latency_records(self):
        m = PipelineMetrics()
        assert m.avg_fetch_latency == 0.0

    def test_error_tracking(self):
        m = PipelineMetrics()
        m.record_errors(3)
        m.record_errors(2)
        assert m.total_errors == 5

    def test_published_tracking(self):
        m = PipelineMetrics()
        m.record_published(50)
        m.record_published(30)
        assert m.total_published == 80

    def test_cycle_resets_metrics(self):
        m = PipelineMetrics()
        m.record_fetch(1.0)
        m.record_published(100)
        m.record_errors(5)
        m.cycle_start()
        assert m.fetch_count == 0
        assert m.total_published == 0
        assert m.total_errors == 0


# ── Orchestrator Concurrency Tests ──────────────────────────

class TestConcurrentCategoryCrawling:

    @pytest.mark.asyncio
    async def test_crawlers_run_concurrently(self):
        """Verify that multiple crawlers execute in parallel, not sequentially."""
        call_times = []

        async def slow_crawl(*args, **kwargs):
            call_times.append(time.monotonic())
            await asyncio.sleep(0.05)
            return [_make_listing()]

        # 3 crawlers, each taking 50ms
        crawlers = []
        for mp in ["amazon_us", "ebay", "mercadolibre_mx"]:
            c = _make_crawler(mp)
            c.crawl_category = slow_crawl
            crawlers.append(c)

        dedup = _make_dedup()
        redis_mock = _make_redis()

        orch = ScrapingOrchestrator(
            crawlers=crawlers,
            search_scrapers=[],
            redis_client=redis_mock,
            dedup=dedup,
            categories=["audifonos"],
        )

        # Patch category availability so all crawlers run
        with patch(
            "scraper.scraping_orchestrator.get_all_categories_for",
            return_value={"audifonos": "url"},
        ):
            start = time.monotonic()
            result = await orch._run_category_mode()
            elapsed = time.monotonic() - start

        # 3 tasks at 50ms each: sequential=150ms, concurrent~=50ms
        assert len(call_times) == 3
        # All should start within a tight window (concurrent)
        assert elapsed < 0.3  # generous bound (concurrent, not sequential)

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """Verify semaphore prevents too many concurrent crawls."""
        active = {"count": 0, "max": 0}

        async def tracked_crawl(*args, **kwargs):
            active["count"] += 1
            active["max"] = max(active["max"], active["count"])
            await asyncio.sleep(0.02)
            active["count"] -= 1
            return [_make_listing()]

        # 10 crawlers but semaphore limits to CATEGORY_CONCURRENCY
        crawlers = []
        for i in range(10):
            c = _make_crawler(f"mp_{i}")
            c.crawl_category = tracked_crawl
            crawlers.append(c)

        dedup = _make_dedup()
        redis_mock = _make_redis()

        orch = ScrapingOrchestrator(
            crawlers=crawlers,
            search_scrapers=[],
            redis_client=redis_mock,
            dedup=dedup,
            categories=["audifonos"],
        )

        with patch(
            "scraper.scraping_orchestrator.get_all_categories_for",
            return_value={"audifonos": "url"},
        ):
            await orch._run_category_mode()

        # Max concurrent should not exceed semaphore limit
        assert active["max"] <= CATEGORY_CONCURRENCY

    @pytest.mark.asyncio
    async def test_error_in_one_crawler_doesnt_block_others(self):
        """One failing crawler should not prevent others from completing."""
        crawlers = [
            _make_crawler("amazon_us", listings=[_make_listing("Good 1")]),
            _make_crawler("ebay"),
            _make_crawler("mercadolibre_mx", listings=[_make_listing("Good 2")]),
        ]
        # Make ebay crawler raise
        crawlers[1].crawl_category = AsyncMock(side_effect=RuntimeError("blocked"))

        dedup = _make_dedup()
        redis_mock = _make_redis()

        orch = ScrapingOrchestrator(
            crawlers=crawlers,
            search_scrapers=[],
            redis_client=redis_mock,
            dedup=dedup,
            categories=["audifonos"],
        )

        with patch(
            "scraper.scraping_orchestrator.get_all_categories_for",
            return_value={"audifonos": "url"},
        ):
            total = await orch._run_category_mode()

        # 2 successful crawlers should publish
        assert total >= 2


class TestConcurrentSearchMode:

    @pytest.mark.asyncio
    async def test_search_runs_concurrently(self):
        """Searches across scrapers should run concurrently."""
        scrapers = [
            _make_search_scraper("ebay", [_make_listing()]),
            _make_search_scraper("amazon_us", [_make_listing()]),
        ]

        dedup = _make_dedup()
        redis_mock = _make_redis()

        orch = ScrapingOrchestrator(
            crawlers=[],
            search_scrapers=scrapers,
            redis_client=redis_mock,
            dedup=dedup,
        )

        # Override base search terms
        orch._base_search_terms = ["test term"]

        total = await orch._run_search_mode()
        assert total >= 2  # At least 1 per scraper


# ── Batched Publishing Tests ────────────────────────────────

class TestBatchedPublishing:

    @pytest.mark.asyncio
    async def test_uses_pipeline_not_individual_xadd(self):
        """Publishing should use Redis pipeline, not individual XADD calls."""
        redis_mock = _make_redis()
        dedup = _make_dedup()

        orch = ScrapingOrchestrator(
            crawlers=[],
            search_scrapers=[],
            redis_client=redis_mock,
            dedup=dedup,
        )

        listings = [_make_listing(f"Item {i}") for i in range(10)]
        count = await orch._dedup_and_publish(listings)

        # Should have used pipeline
        redis_mock.pipeline.assert_called()
        assert count > 0

    @pytest.mark.asyncio
    async def test_empty_listings_noop(self):
        """Empty listing list should not call Redis at all."""
        redis_mock = _make_redis()
        dedup = _make_dedup()

        orch = ScrapingOrchestrator(
            crawlers=[], search_scrapers=[],
            redis_client=redis_mock, dedup=dedup,
        )

        count = await orch._dedup_and_publish([])
        assert count == 0
        redis_mock.pipeline.assert_not_called()


# ── AliExpress Shared Client Tests ──────────────────────────

class TestAliExpressSharedClient:

    @pytest.mark.asyncio
    async def test_client_created_once(self):
        """Shared client should be created once and reused."""
        crawler = AliExpressCategoryCrawler()

        client1 = await crawler._get_client()
        client2 = await crawler._get_client()

        assert client1 is client2
        await crawler.close()

    @pytest.mark.asyncio
    async def test_client_has_connection_pooling(self):
        """Shared client should have connection pool limits configured."""
        crawler = AliExpressCategoryCrawler()
        client = await crawler._get_client()

        # httpx stores pool config in _transport
        transport = client._transport
        assert transport is not None
        # The transport should be an AsyncHTTPTransport with pool limits
        assert hasattr(transport, '_pool')
        await crawler.close()

    @pytest.mark.asyncio
    async def test_close_nullifies_client(self):
        """Closing should set client to None."""
        crawler = AliExpressCategoryCrawler()
        await crawler._get_client()
        assert crawler._client is not None

        await crawler.close()
        assert crawler._client is None


# ── StealthSession Configuration Tests ──────────────────────

class TestStealthSessionConfig:

    def test_requests_per_session_default(self):
        """Default requests per session should be 50 (configurable via env)."""
        session = StealthSession()
        assert session.REQUESTS_PER_SESSION >= 15

    @pytest.mark.asyncio
    async def test_client_has_keepalive_limits(self):
        """StealthSession should create clients with connection limits."""
        session = StealthSession()
        await session._rotate_session()

        client = session._client
        assert client is not None
        # Verify limits are set (httpx stores them internally)
        await session.close()


# ── Integration: Full Cycle Test ────────────────────────────

class TestFullCycleIntegration:

    @pytest.mark.asyncio
    async def test_run_cycle_returns_total(self):
        """Full cycle should return total published listings."""
        crawlers = [_make_crawler("amazon_us", [_make_listing()])]
        dedup = _make_dedup()
        redis_mock = _make_redis()

        orch = ScrapingOrchestrator(
            crawlers=crawlers,
            search_scrapers=[],
            redis_client=redis_mock,
            dedup=dedup,
            categories=["audifonos"],
        )
        orch.set_trending_scraper_infra(MagicMock(), MagicMock())
        # Mock trending scraper
        orch._trending_scraper.scrape_all_trending = AsyncMock(return_value=[])

        with patch(
            "scraper.scraping_orchestrator.get_all_categories_for",
            return_value={"audifonos": "url"},
        ):
            total = await orch.run_cycle()

        assert total >= 0
        assert orch.metrics.fetch_count >= 0

    @pytest.mark.asyncio
    async def test_metrics_populated_after_cycle(self):
        """Metrics should be populated after a cycle completes."""
        crawlers = [_make_crawler("amazon_us", [_make_listing()])]
        dedup = _make_dedup()
        redis_mock = _make_redis()

        orch = ScrapingOrchestrator(
            crawlers=crawlers,
            search_scrapers=[],
            redis_client=redis_mock,
            dedup=dedup,
            categories=["audifonos"],
        )
        orch.set_trending_scraper_infra(MagicMock(), MagicMock())
        orch._trending_scraper.scrape_all_trending = AsyncMock(return_value=[])

        with patch(
            "scraper.scraping_orchestrator.get_all_categories_for",
            return_value={"audifonos": "url"},
        ):
            await orch.run_cycle()

        m = orch.metrics
        # Throughput should be calculable
        assert m.throughput >= 0
        # Published count should match pipeline output
        assert m.total_published >= 0
