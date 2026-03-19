"""
Scraping orchestrator — rotates between three scraping modes:

1. Category crawling (60%) — deep pagination through marketplace categories
2. Search expansion (25%) — expanded keyword searches
3. Trending discovery (15%) — bestsellers, deals, trending products

The orchestrator manages the rotation, tracks results per mode,
and feeds all listings through the standard pipeline (dedup → Redis).

v2 optimizations:
- Concurrent category crawling with configurable semaphore
- Batched Redis publishing (pipeline, not one-by-one)
- Per-cycle latency and throughput metrics
- Concurrent search across scrapers per term
"""
import asyncio
import logging
import os
import random
import time

import redis.asyncio as redis

from .category_config import get_all_categories_for, get_common_categories, DEFAULT_CATEGORIES
from .category_prioritizer import CategoryPrioritizer
from .dedup import DedupFilter
from .keyword_expander import expand_all_terms, ARBITRAGE_SEARCH_TERMS
from .schemas import RawListing
from .subcategory_discovery import SubcategoryDiscovery
from .trending_scraper import TrendingScraper

logger = logging.getLogger(__name__)

# ── Mode distribution (configurable via env) ─────────────────
CATEGORY_WEIGHT = float(os.getenv("WEIGHT_CATEGORY", "0.60"))
SEARCH_WEIGHT = float(os.getenv("WEIGHT_SEARCH", "0.25"))
TRENDING_WEIGHT = float(os.getenv("WEIGHT_TRENDING", "0.15"))

# Category config
CATEGORY_MAX_PAGES = int(os.getenv("CATEGORY_MAX_PAGES", "10"))
SUBCATEGORY_DISCOVERY = os.getenv("SUBCATEGORY_DISCOVERY", "true").lower() == "true"
SUBCATEGORY_MAX_PAGES = int(os.getenv("SUBCATEGORY_MAX_PAGES", "5"))

# Concurrency config
CATEGORY_CONCURRENCY = int(os.getenv("CATEGORY_CONCURRENCY", "6"))
SEARCH_CONCURRENCY = int(os.getenv("SEARCH_CONCURRENCY", "4"))
PUBLISH_CHUNK_SIZE = int(os.getenv("PUBLISH_CHUNK_SIZE", "100"))

# Search config
SEARCH_MAX_RESULTS = int(os.getenv("SEARCH_MAX_RESULTS", "30"))
SEARCH_TERMS_ENV = os.getenv("SEARCH_TERMS", "")

# Trending config
TRENDING_MAX_PAGES = int(os.getenv("TRENDING_MAX_PAGES", "2"))

STREAM_KEY = "raw_listings_queue"


class ScrapingOrchestrator:
    """
    Coordinates all scraping modes in a weighted rotation.

    Each cycle allocates time to each mode proportionally:
    - 60% category crawling (with subcategory discovery)
    - 25% expanded keyword search
    - 15% trending/bestseller scraping

    v2: Concurrent crawling with semaphore-based concurrency control,
    batched Redis publishing, and per-cycle metrics.
    """

    def __init__(
        self,
        crawlers: list,
        search_scrapers: list | None,
        redis_client: redis.Redis,
        dedup: DedupFilter,
        categories: list[str] | None = None,
    ):
        self._crawlers = crawlers
        self._search_scrapers = search_scrapers or []
        self._redis = redis_client
        self._dedup = dedup
        self._categories = categories or DEFAULT_CATEGORIES

        # Concurrency control
        self._category_sem = asyncio.Semaphore(CATEGORY_CONCURRENCY)
        self._search_sem = asyncio.Semaphore(SEARCH_CONCURRENCY)

        # Sub-components
        self._prioritizer = CategoryPrioritizer(base_pages=CATEGORY_MAX_PAGES)
        self._subcategory_discovery = SubcategoryDiscovery() if SUBCATEGORY_DISCOVERY else None
        self._trending_scraper = TrendingScraper(
            rate_limiter=None,
            proxy_pool=None,
        )

        # Search terms — combine env terms with built-in arbitrage terms
        base_terms = []
        if SEARCH_TERMS_ENV:
            base_terms = [t.strip() for t in SEARCH_TERMS_ENV.split(",") if t.strip()]
        base_terms.extend(ARBITRAGE_SEARCH_TERMS)
        # Deduplicate
        seen = set()
        unique_terms = []
        for t in base_terms:
            if t.lower() not in seen:
                seen.add(t.lower())
                unique_terms.append(t)
        self._base_search_terms = unique_terms

        # Cycle stats
        self._cycle_stats = {
            "category": {"listings": 0, "time": 0.0},
            "search": {"listings": 0, "time": 0.0},
            "trending": {"listings": 0, "time": 0.0},
        }

        # Metrics
        self._metrics = PipelineMetrics()

    def set_trending_scraper_infra(self, rate_limiter, proxy_pool):
        """Set shared infrastructure on trending scraper after construction."""
        self._trending_scraper._rate_limiter = rate_limiter
        self._trending_scraper._proxy_pool = proxy_pool

    @property
    def metrics(self) -> "PipelineMetrics":
        return self._metrics

    async def run_cycle(self) -> int:
        """
        Run one full scraping cycle across all modes.
        Returns total unique listings published.
        """
        self._dedup.reset()
        self._reset_stats()
        self._metrics.cycle_start()

        if self._subcategory_discovery:
            self._subcategory_discovery.reset()

        total = 0

        # Run all scraping modes in parallel
        cat_listings, search_listings, trend_listings = await asyncio.gather(
            self._run_category_mode(),
            self._run_search_mode(),
            self._run_trending_mode(),
        )
        total += cat_listings + search_listings + trend_listings

        # Recalculate priorities for next cycle
        self._prioritizer.recalculate_priorities()

        self._metrics.cycle_end(total)
        self._log_cycle_summary(total)
        return total

    # ── Category mode (concurrent) ────────────────────────────

    async def _run_category_mode(self) -> int:
        """Crawl categories concurrently with semaphore-based throttling."""
        start = time.monotonic()

        # Get prioritized category order
        categories = self._prioritizer.get_prioritized_categories(self._categories)

        # Build all (category, crawler) tasks upfront
        tasks: list[tuple[str, object, int]] = []
        for category_slug in categories:
            for crawler in self._crawlers:
                available = get_all_categories_for(crawler.marketplace_id)
                if category_slug not in available:
                    continue
                max_pages = self._prioritizer.get_pages(category_slug)
                tasks.append((category_slug, crawler, max_pages))

        if not tasks:
            return 0

        logger.info(
            "[category] Launching %d crawl tasks (concurrency=%d)",
            len(tasks), CATEGORY_CONCURRENCY,
        )

        # Run all tasks concurrently with semaphore
        results = await asyncio.gather(
            *(
                self._bounded_category_crawl(cat, crawler, pages)
                for cat, crawler, pages in tasks
            ),
            return_exceptions=True,
        )

        total = 0
        for i, result in enumerate(results):
            if isinstance(result, int):
                total += result
            elif isinstance(result, Exception):
                cat, crawler, _ = tasks[i]
                logger.error(
                    "Error crawling '%s' on %s: %s",
                    cat, crawler.marketplace_id, result,
                )

        self._cycle_stats["category"]["listings"] = total
        self._cycle_stats["category"]["time"] = time.monotonic() - start
        return total

    async def _bounded_category_crawl(
        self, category_slug: str, crawler, max_pages: int,
    ) -> int:
        """Crawl a single category with semaphore-bounded concurrency."""
        async with self._category_sem:
            t0 = time.monotonic()
            logger.info(
                "[category] Crawling '%s' on %s (%d pages)...",
                category_slug, crawler.marketplace_id, max_pages,
            )

            listings = await crawler.crawl_category(
                category_slug, max_pages=max_pages,
            )
            self._metrics.record_fetch(time.monotonic() - t0)

            # Subcategory discovery
            if (
                self._subcategory_discovery
                and hasattr(crawler, '_last_html')
                and crawler._last_html
            ):
                await self._discover_and_crawl_subcategories(
                    crawler, category_slug,
                )

            # Dedup and publish (batched)
            published = await self._dedup_and_publish(listings)

            self._prioritizer.record_scrape(
                category_slug, published, max_pages,
            )

            logger.info(
                "[category] %s/%s: %d scraped → %d published",
                crawler.marketplace_id, category_slug,
                len(listings), published,
            )
            return published

    async def _discover_and_crawl_subcategories(
        self, crawler, parent_slug: str,
    ) -> None:
        """Discover and crawl subcategories found on the parent page."""
        if not self._subcategory_discovery:
            return

        try:
            html = crawler._last_html
            base_url = crawler._last_url if hasattr(crawler, '_last_url') else ""
            if not html or not base_url:
                return

            subcats = self._subcategory_discovery.extract_subcategories(
                html, crawler.marketplace_id, base_url,
            )

            for subcat in subcats[:10]:  # Limit subcategories per parent
                logger.info(
                    "[subcategory] Crawling discovered '%s' on %s",
                    subcat["slug"], crawler.marketplace_id,
                )
                try:
                    # Use the raw URL directly with a limited page count
                    if hasattr(crawler, 'crawl_url'):
                        listings = await crawler.crawl_url(
                            subcat["url"], max_pages=SUBCATEGORY_MAX_PAGES,
                        )
                    else:
                        # Fallback — skip if crawler doesn't support URL crawling
                        continue

                    published = await self._dedup_and_publish(listings)
                    logger.info(
                        "[subcategory] %s/%s: %d published",
                        crawler.marketplace_id, subcat["slug"], published,
                    )
                except Exception:
                    logger.error(
                        "Subcategory crawl failed: %s/%s",
                        crawler.marketplace_id, subcat["slug"], exc_info=True,
                    )
        except Exception:
            logger.error("Subcategory discovery failed", exc_info=True)

    # ── Search mode (concurrent) ─────────────────────────────

    async def _run_search_mode(self) -> int:
        """Run expanded keyword searches concurrently."""
        if not self._search_scrapers:
            return 0

        start = time.monotonic()

        # Expand search terms
        expanded = expand_all_terms(
            self._base_search_terms,
            max_variants_per_term=4,
        )

        # Shuffle to vary coverage across cycles
        random.shuffle(expanded)

        # Limit total searches per cycle to stay within time budget
        max_searches = int(len(expanded) * SEARCH_WEIGHT / max(CATEGORY_WEIGHT, 0.01))
        terms_this_cycle = expanded[:max(max_searches, 20)]

        logger.info(
            "[search] Running %d expanded terms across %d scrapers (concurrency=%d)",
            len(terms_this_cycle), len(self._search_scrapers), SEARCH_CONCURRENCY,
        )

        # Build all (term, scraper) tasks
        search_tasks = [
            (term, scraper)
            for term in terms_this_cycle
            for scraper in self._search_scrapers
        ]

        results = await asyncio.gather(
            *(
                self._bounded_search(term, scraper)
                for term, scraper in search_tasks
            ),
            return_exceptions=True,
        )

        total = sum(r for r in results if isinstance(r, int))
        errors = sum(1 for r in results if isinstance(r, Exception))
        if errors:
            self._metrics.record_errors(errors)
            logger.warning("[search] %d/%d search tasks failed", errors, len(search_tasks))

        self._cycle_stats["search"]["listings"] = total
        self._cycle_stats["search"]["time"] = time.monotonic() - start
        return total

    async def _bounded_search(self, term: str, scraper) -> int:
        """Run a single search with semaphore-bounded concurrency."""
        async with self._search_sem:
            t0 = time.monotonic()
            listings = await scraper.scrape(
                term, max_results=SEARCH_MAX_RESULTS,
            )
            self._metrics.record_fetch(time.monotonic() - t0)
            published = await self._dedup_and_publish(listings)
            if published > 0:
                logger.debug(
                    "[search] '%s' on %s: %d published",
                    term, scraper.marketplace_id, published,
                )
            return published

    # ── Trending mode ────────────────────────────────────────

    async def _run_trending_mode(self) -> int:
        """Scrape trending/bestseller pages."""
        start = time.monotonic()

        # Only scrape trending for marketplaces we have crawlers for
        marketplace_ids = [c.marketplace_id for c in self._crawlers]

        try:
            listings = await self._trending_scraper.scrape_all_trending(
                marketplaces=marketplace_ids,
                max_pages_per_section=TRENDING_MAX_PAGES,
            )
        except Exception:
            logger.error("Trending scrape failed", exc_info=True)
            listings = []

        published = await self._dedup_and_publish(listings)

        self._cycle_stats["trending"]["listings"] = published
        self._cycle_stats["trending"]["time"] = time.monotonic() - start
        return published

    # ── Shared helpers ───────────────────────────────────────

    async def _dedup_and_publish(self, listings: list[RawListing]) -> int:
        """Deduplicate and batch-publish listings to Redis stream.

        Uses Redis pipeline to send PUBLISH_CHUNK_SIZE XADDs per
        round-trip instead of one-by-one.
        """
        if not listings:
            return 0

        unique = await self._dedup.filter_batch(listings)
        if not unique:
            return 0

        count = 0
        for i in range(0, len(unique), PUBLISH_CHUNK_SIZE):
            chunk = unique[i : i + PUBLISH_CHUNK_SIZE]
            try:
                async with self._redis.pipeline(transaction=False) as pipe:
                    for listing in chunk:
                        pipe.xadd(STREAM_KEY, listing.to_stream_dict())
                    results = await pipe.execute()
                    count += sum(1 for r in results if r is not None)
            except Exception:
                # Fallback: publish remaining one by one
                for listing in chunk:
                    try:
                        await self._redis.xadd(STREAM_KEY, listing.to_stream_dict())
                        count += 1
                    except Exception:
                        logger.error(
                            "Failed to publish: %s", listing.title[:40], exc_info=True,
                        )
        self._metrics.record_published(count)
        return count

    def _reset_stats(self) -> None:
        """Reset per-cycle stats."""
        for mode in self._cycle_stats:
            self._cycle_stats[mode] = {"listings": 0, "time": 0.0}

    def _log_cycle_summary(self, total: int) -> None:
        """Log a summary of the cycle results with metrics."""
        parts = []
        for mode, stats in self._cycle_stats.items():
            pct = (stats["listings"] / max(total, 1)) * 100
            parts.append(
                f"{mode}: {stats['listings']} ({pct:.0f}%, {stats['time']:.0f}s)"
            )

        logger.info("Cycle summary: %d total | %s", total, " | ".join(parts))

        # Pipeline metrics
        m = self._metrics
        logger.info(
            "Pipeline: %.1f items/sec | avg_fetch=%.2fs | errors=%d | published=%d",
            m.throughput, m.avg_fetch_latency, m.total_errors, m.total_published,
        )

        logger.info(
            "Dedup pool: %d hashes | Subcategories discovered: %d",
            self._dedup.local_count,
            self._subcategory_discovery.total_discovered if self._subcategory_discovery else 0,
        )

        # Log prioritizer summary
        summary = self._prioritizer.stats_summary
        if summary:
            top = sorted(
                summary.items(),
                key=lambda x: x[1]["score"],
                reverse=True,
            )[:5]
            logger.info(
                "Top categories: %s",
                [(s, f"score={d['score']:.1f} pages={d['pages']}") for s, d in top],
            )


class PipelineMetrics:
    """Lightweight metrics tracker for the scraping pipeline."""

    def __init__(self):
        self._cycle_start: float = 0.0
        self._cycle_elapsed: float = 0.0
        self._fetch_latencies: list[float] = []
        self._total_published: int = 0
        self._total_errors: int = 0
        self._total_items: int = 0

    def cycle_start(self) -> None:
        self._cycle_start = time.monotonic()
        self._fetch_latencies.clear()
        self._total_published = 0
        self._total_errors = 0
        self._total_items = 0

    def cycle_end(self, total_items: int) -> None:
        self._cycle_elapsed = time.monotonic() - self._cycle_start
        self._total_items = total_items

    def record_fetch(self, latency: float) -> None:
        self._fetch_latencies.append(latency)

    def record_published(self, count: int) -> None:
        self._total_published += count

    def record_errors(self, count: int) -> None:
        self._total_errors += count

    @property
    def throughput(self) -> float:
        """Items per second for the current cycle."""
        if self._cycle_elapsed <= 0:
            return 0.0
        return self._total_items / self._cycle_elapsed

    @property
    def avg_fetch_latency(self) -> float:
        if not self._fetch_latencies:
            return 0.0
        return sum(self._fetch_latencies) / len(self._fetch_latencies)

    @property
    def total_errors(self) -> int:
        return self._total_errors

    @property
    def total_published(self) -> int:
        return self._total_published

    @property
    def fetch_count(self) -> int:
        return len(self._fetch_latencies)
