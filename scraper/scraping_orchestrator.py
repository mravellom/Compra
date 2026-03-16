"""
Scraping orchestrator — rotates between three scraping modes:

1. Category crawling (60%) — deep pagination through marketplace categories
2. Search expansion (25%) — expanded keyword searches
3. Trending discovery (15%) — bestsellers, deals, trending products

The orchestrator manages the rotation, tracks results per mode,
and feeds all listings through the standard pipeline (dedup → Redis).
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

    def set_trending_scraper_infra(self, rate_limiter, proxy_pool):
        """Set shared infrastructure on trending scraper after construction."""
        self._trending_scraper._rate_limiter = rate_limiter
        self._trending_scraper._proxy_pool = proxy_pool

    async def run_cycle(self) -> int:
        """
        Run one full scraping cycle across all modes.
        Returns total unique listings published.
        """
        self._dedup.reset()
        self._reset_stats()

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

        self._log_cycle_summary(total)
        return total

    # ── Category mode ─────────────────────────────────────────

    async def _run_category_mode(self) -> int:
        """Crawl categories with pagination and optional subcategory discovery."""
        start = time.monotonic()
        total = 0

        # Get prioritized category order
        categories = self._prioritizer.get_prioritized_categories(self._categories)

        for category_slug in categories:
            for crawler in self._crawlers:
                available = get_all_categories_for(crawler.marketplace_id)
                if category_slug not in available:
                    continue

                # Get dynamic page allocation
                max_pages = self._prioritizer.get_pages(category_slug)

                logger.info(
                    "[category] Crawling '%s' on %s (%d pages)...",
                    category_slug, crawler.marketplace_id, max_pages,
                )

                try:
                    listings = await crawler.crawl_category(
                        category_slug, max_pages=max_pages,
                    )
                except Exception:
                    logger.error(
                        "Error crawling '%s' on %s",
                        category_slug, crawler.marketplace_id, exc_info=True,
                    )
                    continue

                # Subcategory discovery
                if (
                    self._subcategory_discovery
                    and hasattr(crawler, '_last_html')
                    and crawler._last_html
                ):
                    await self._discover_and_crawl_subcategories(
                        crawler, category_slug,
                    )

                # Dedup and publish
                published = await self._dedup_and_publish(listings)
                total += published

                self._prioritizer.record_scrape(
                    category_slug, published, max_pages,
                )

                logger.info(
                    "[category] %s/%s: %d scraped → %d published",
                    crawler.marketplace_id, category_slug,
                    len(listings), published,
                )

        self._cycle_stats["category"]["listings"] = total
        self._cycle_stats["category"]["time"] = time.monotonic() - start
        return total

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

    # ── Search mode ──────────────────────────────────────────

    async def _run_search_mode(self) -> int:
        """Run expanded keyword searches."""
        if not self._search_scrapers:
            return 0

        start = time.monotonic()
        total = 0

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
            "[search] Running %d expanded terms across %d scrapers",
            len(terms_this_cycle), len(self._search_scrapers),
        )

        for term in terms_this_cycle:
            for scraper in self._search_scrapers:
                try:
                    listings = await scraper.scrape(
                        term, max_results=SEARCH_MAX_RESULTS,
                    )
                    published = await self._dedup_and_publish(listings)
                    total += published

                    if published > 0:
                        logger.debug(
                            "[search] '%s' on %s: %d published",
                            term, scraper.marketplace_id, published,
                        )
                except Exception:
                    logger.error(
                        "Search failed: '%s' on %s",
                        term, scraper.marketplace_id, exc_info=True,
                    )

                await asyncio.sleep(1)  # Brief pause between searches

        self._cycle_stats["search"]["listings"] = total
        self._cycle_stats["search"]["time"] = time.monotonic() - start
        return total

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
        """Deduplicate and publish listings to Redis stream."""
        if not listings:
            return 0

        unique = await self._dedup.filter_batch(listings)
        count = 0
        for listing in unique:
            try:
                await self._redis.xadd(STREAM_KEY, listing.to_stream_dict())
                count += 1
            except Exception:
                logger.error(
                    "Failed to publish: %s", listing.title[:40], exc_info=True,
                )
        return count

    def _reset_stats(self) -> None:
        """Reset per-cycle stats."""
        for mode in self._cycle_stats:
            self._cycle_stats[mode] = {"listings": 0, "time": 0.0}

    def _log_cycle_summary(self, total: int) -> None:
        """Log a summary of the cycle results."""
        parts = []
        for mode, stats in self._cycle_stats.items():
            pct = (stats["listings"] / max(total, 1)) * 100
            parts.append(
                f"{mode}: {stats['listings']} ({pct:.0f}%, {stats['time']:.0f}s)"
            )

        logger.info("Cycle summary: %d total | %s", total, " | ".join(parts))
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
