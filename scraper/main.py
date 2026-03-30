"""
Scraper entrypoint — multi-mode scraping architecture.

Modes:
  SCRAPER_MODE=orchestrated (default) — category + search + trending rotation
  SCRAPER_MODE=category   — category-only crawling (legacy)
  SCRAPER_MODE=search     — keyword search only (legacy)

The orchestrated mode rotates between:
  60% category crawling (with subcategory discovery + prioritization)
  25% expanded keyword search
  15% trending/bestseller discovery

Target: 60,000–120,000 listings per cycle.
"""
import asyncio
import logging
import os
import signal
import sys
import time

from dotenv import load_dotenv
load_dotenv()

import redis.asyncio as redis

from .amazon_category_crawler import AmazonCategoryCrawler
from .amazon_us_category_crawler import AmazonUSCategoryCrawler
from .aliexpress_category_crawler import AliExpressCategoryCrawler
from .ml_category_crawler import MLCategoryCrawler
from .category_config import (
    DEFAULT_CATEGORIES,
    DEFAULT_MAX_PAGES,
    get_all_categories_for,
    get_common_categories,
)
from .dedup import DedupFilter
from .rate_limiter import RateLimiter
from .schemas import RawListing
from .stealth import ProxyPool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM_KEY = "raw_listings_queue"
SCRAPE_INTERVAL = int(os.getenv("SCRAPE_INTERVAL", "300"))
SCRAPER_MODE = os.getenv("SCRAPER_MODE", "orchestrated")

# Search mode config (legacy)
SEARCH_TERMS = os.getenv("SEARCH_TERMS", "sony wh-1000xm4,airpods pro,nintendo switch")
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "20"))

# Category mode config
CATEGORIES = os.getenv("CATEGORIES", "")
CATEGORY_MAX_PAGES = int(os.getenv("CATEGORY_MAX_PAGES", str(DEFAULT_MAX_PAGES)))

# Smart expansion: boost pages for high-yield categories
SMART_EXPANSION = os.getenv("SMART_EXPANSION", "true").lower() == "true"
EXPANSION_MULTIPLIER = float(os.getenv("EXPANSION_MULTIPLIER", "1.5"))

# Stats tracking for smart expansion (category-only mode)
_category_stats: dict[str, dict] = {}


async def publish_to_stream(redis_client: redis.Redis, listing: RawListing) -> str:
    msg_id = await redis_client.xadd(STREAM_KEY, listing.to_stream_dict())
    return msg_id


# ── Batch size for Redis pipeline (number of XADDs per round-trip) ──
_PUBLISH_CHUNK_SIZE = int(os.getenv("PUBLISH_CHUNK_SIZE", "100"))


_WAL_DIR = os.path.join(os.path.dirname(__file__), ".wal")
_WAL_ENABLED = os.getenv("SCRAPER_WAL", "true").lower() in ("1", "true", "yes")


_WAL_MAX_BYTES = int(os.getenv("SCRAPER_WAL_MAX_BYTES", str(500 * 1024 * 1024)))  # 500MB


def _save_to_wal(listings: list[RawListing]) -> None:
    """Save failed listings to local WAL file for later replay."""
    import json
    import uuid
    os.makedirs(_WAL_DIR, exist_ok=True)

    # Check WAL size before writing — prevent disk exhaustion
    total_size = sum(
        os.path.getsize(os.path.join(_WAL_DIR, f))
        for f in os.listdir(_WAL_DIR) if f.endswith(".jsonl")
    ) if os.path.isdir(_WAL_DIR) else 0
    if total_size > _WAL_MAX_BYTES:
        logger.critical(
            "WAL: disk limit reached (%.0f MB > %.0f MB). Dropping %d listings!",
            total_size / 1e6, _WAL_MAX_BYTES / 1e6, len(listings),
        )
        return

    # Use UUID to prevent filename collisions across processes
    wal_file = os.path.join(_WAL_DIR, f"failed_{int(time.time())}_{uuid.uuid4().hex[:8]}.jsonl")
    with open(wal_file, "w") as f:
        for listing in listings:
            f.write(json.dumps(listing.to_stream_dict()) + "\n")
    logger.warning("WAL: saved %d listings to %s for later replay", len(listings), wal_file)


async def replay_wal(redis_client: redis.Redis) -> int:
    """Replay any saved WAL files back to Redis. Call on startup."""
    import json
    if not os.path.isdir(_WAL_DIR):
        return 0
    replayed = 0
    for fname in sorted(os.listdir(_WAL_DIR)):
        fpath = os.path.join(_WAL_DIR, fname)
        if not fname.endswith(".jsonl"):
            continue
        try:
            valid_entries = []
            with open(fpath) as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        valid_entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning("WAL: corrupt line %d in %s, skipping", line_num, fname)
            if valid_entries:
                async with redis_client.pipeline(transaction=False) as pipe:
                    for data in valid_entries:
                        pipe.xadd(STREAM_KEY, data)
                    await pipe.execute()
                replayed += len(valid_entries)
            os.remove(fpath)
            logger.info("WAL: replayed %d listings from %s", len(valid_entries), fname)
        except Exception:
            logger.error("WAL: failed to replay %s", fname, exc_info=True)
    return replayed


async def publish_batch(redis_client: redis.Redis, listings: list[RawListing]) -> int:
    """Publish a batch of listings to Redis using pipeline batching.

    Groups listings into chunks and sends each chunk in a single Redis
    pipeline round-trip (N XADDs per pipeline.execute() instead of N
    individual round-trips).

    If Redis is down, saves to local WAL for later replay.
    """
    if not listings:
        return 0

    count = 0
    for i in range(0, len(listings), _PUBLISH_CHUNK_SIZE):
        chunk = listings[i : i + _PUBLISH_CHUNK_SIZE]
        try:
            async with redis_client.pipeline(transaction=False) as pipe:
                for listing in chunk:
                    pipe.xadd(STREAM_KEY, listing.to_stream_dict())
                results = await pipe.execute()
                count += sum(1 for r in results if r is not None)
        except Exception:
            # Fallback: try one by one
            chunk_failed = []
            for listing in chunk:
                try:
                    await redis_client.xadd(STREAM_KEY, listing.to_stream_dict())
                    count += 1
                except Exception:
                    chunk_failed.append(listing)
            # Save failures to WAL if enabled
            if chunk_failed and _WAL_ENABLED:
                _save_to_wal(chunk_failed)
            elif chunk_failed:
                logger.error("Lost %d listings (WAL disabled)", len(chunk_failed))
    return count


def _get_smart_pages(category_slug: str, base_pages: int) -> int:
    """Increase pages for categories that historically produce good listings."""
    if not SMART_EXPANSION:
        return base_pages

    stats = _category_stats.get(category_slug)
    if not stats:
        return base_pages

    avg_per_page = stats.get("avg_listings_per_page", 0)
    if avg_per_page > 30:
        return min(int(base_pages * EXPANSION_MULTIPLIER), 50)
    if avg_per_page < 5:
        return max(base_pages // 2, 2)

    return base_pages


def _update_stats(category_slug: str, marketplace: str, listings_count: int, pages: int):
    """Track category performance for smart expansion."""
    key = category_slug
    if key not in _category_stats:
        _category_stats[key] = {"total_listings": 0, "total_pages": 0, "marketplaces": set()}

    _category_stats[key]["total_listings"] += listings_count
    _category_stats[key]["total_pages"] += pages
    _category_stats[key]["marketplaces"].add(marketplace)

    total_pages = _category_stats[key]["total_pages"]
    if total_pages > 0:
        _category_stats[key]["avg_listings_per_page"] = (
            _category_stats[key]["total_listings"] / total_pages
        )


# ── Category mode (legacy) ───────────────────────────────────
async def run_category_cycle(
    crawlers: list,
    redis_client: redis.Redis,
    categories: list[str],
    dedup: DedupFilter,
) -> int:
    """Run one full category crawling cycle across all marketplaces."""
    total = 0
    cycle_start = time.monotonic()

    for category_slug in categories:
        for crawler in crawlers:
            # Skip if marketplace doesn't have this category
            available = get_all_categories_for(crawler.marketplace_id)
            if category_slug not in available:
                continue

            max_pages = _get_smart_pages(category_slug, CATEGORY_MAX_PAGES)

            logger.info(
                "Crawling '%s' on %s (max %d pages)...",
                category_slug, crawler.marketplace_id, max_pages,
            )

            try:
                listings = await crawler.crawl_category(
                    category_slug, max_pages=max_pages
                )
            except Exception:
                logger.error(
                    "Error crawling '%s' on %s",
                    category_slug, crawler.marketplace_id, exc_info=True,
                )
                continue

            # Deduplicate
            unique_listings = await dedup.filter_batch(listings)
            dupes = len(listings) - len(unique_listings)

            published = await publish_batch(redis_client, unique_listings)
            total += published

            _update_stats(category_slug, crawler.marketplace_id, len(unique_listings), max_pages)

            logger.info(
                "[%s] '%s': %d scraped, %d unique, %d dupes, %d published",
                crawler.marketplace_id, category_slug,
                len(listings), len(unique_listings), dupes, published,
            )

    elapsed = time.monotonic() - cycle_start
    logger.info(
        "Category cycle complete: %d listings in %.1fs (dedup pool: %d hashes)",
        total, elapsed, dedup.local_count,
    )

    return total


# ── Search mode (legacy) ──────────────────────────────────
async def run_search_cycle(scrapers: list, redis_client: redis.Redis) -> int:
    terms = [t.strip() for t in SEARCH_TERMS.split(",") if t.strip()]
    total = 0

    for term in terms:
        for scraper in scrapers:
            logger.info("Scraping '%s' on %s...", term, scraper.marketplace_id)
            listings = await scraper.scrape(term, max_results=MAX_RESULTS)
            total += await publish_batch(redis_client, listings)
            await asyncio.sleep(2)

    return total


def _build_category_list() -> list[str]:
    """Determine which categories to crawl."""
    env_cats = CATEGORIES.strip()
    if env_cats:
        return [c.strip() for c in env_cats.split(",") if c.strip()]

    # Use common categories (present in 2+ marketplaces) for best arbitrage coverage
    common = get_common_categories()
    if common:
        return common

    return DEFAULT_CATEGORIES


def _build_crawlers(
    rate_limiter: RateLimiter, proxy_pool: ProxyPool
) -> list:
    """Build all category crawlers for the 7 marketplaces."""
    crawlers = [
        MLCategoryCrawler("mercadolibre_ar", rate_limiter=rate_limiter, proxy_pool=proxy_pool),
        MLCategoryCrawler("mercadolibre_mx", rate_limiter=rate_limiter, proxy_pool=proxy_pool),
        MLCategoryCrawler("mercadolibre_cl", rate_limiter=rate_limiter, proxy_pool=proxy_pool),
        MLCategoryCrawler("mercadolibre_co", rate_limiter=rate_limiter, proxy_pool=proxy_pool),
        AmazonCategoryCrawler(rate_limiter=rate_limiter, proxy_pool=proxy_pool),
        AmazonUSCategoryCrawler(rate_limiter=rate_limiter, proxy_pool=proxy_pool),
    ]
    # AliExpress requires residential proxies — enable when PROXY_URLS is set
    if proxy_pool.has_proxies:
        crawlers.append(AliExpressCategoryCrawler(rate_limiter=rate_limiter, proxy_pool=proxy_pool))
    else:
        logger.info("AliExpress crawler disabled (needs proxies, set PROXY_URLS)")
    return crawlers


def _build_search_scrapers(
    rate_limiter: RateLimiter, proxy_pool: ProxyPool
) -> list:
    """Build legacy search scrapers."""
    from .amazon_scraper import AmazonScraper
    from .amazon_us_scraper import AmazonUSScraper
    from .aliexpress_scraper import AliExpressScraper
    from .ebay_scraper import EbayScraper
    from .mercadolibre_scraper import MercadoLibreScraper

    return [
        MercadoLibreScraper("mercadolibre_ar", rate_limiter=rate_limiter, proxy_pool=proxy_pool),
        MercadoLibreScraper("mercadolibre_mx", rate_limiter=rate_limiter, proxy_pool=proxy_pool),
        MercadoLibreScraper("mercadolibre_cl", rate_limiter=rate_limiter, proxy_pool=proxy_pool),
        MercadoLibreScraper("mercadolibre_co", rate_limiter=rate_limiter, proxy_pool=proxy_pool),
        AmazonScraper(rate_limiter=rate_limiter, proxy_pool=proxy_pool),
        AmazonUSScraper(rate_limiter=rate_limiter, proxy_pool=proxy_pool),
        AliExpressScraper(rate_limiter=rate_limiter, proxy_pool=proxy_pool),
        EbayScraper(rate_limiter=rate_limiter, proxy_pool=proxy_pool),
    ]


async def main() -> None:
    # Redis connection
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await redis_client.ping()
        logger.info("Connected to Redis at %s", REDIS_URL)
    except redis.ConnectionError:
        logger.error("Cannot connect to Redis at %s", REDIS_URL)
        sys.exit(1)

    logger.info("Scraper mode: %s", SCRAPER_MODE)

    # Replay any WAL files from previous failed Redis publishes
    wal_count = await replay_wal(redis_client)
    if wal_count:
        logger.info("WAL replay: %d listings recovered from previous failures", wal_count)

    # Shared infra
    rate_limiter = RateLimiter(requests_per_second=0.5, burst=2)
    proxy_pool = ProxyPool()

    if proxy_pool.has_proxies:
        logger.info("Proxy pool initialized with %d proxies", proxy_pool.size)
    else:
        logger.info("No proxies configured (set PROXY_URLS env var for proxy rotation)")

    # ── Orchestrated mode (default — full multi-mode rotation) ──
    if SCRAPER_MODE == "orchestrated":
        from .scraping_orchestrator import ScrapingOrchestrator

        categories = _build_category_list()
        crawlers = _build_crawlers(rate_limiter, proxy_pool)
        search_scrapers = _build_search_scrapers(rate_limiter, proxy_pool)
        dedup = DedupFilter(redis_client)

        orchestrator = ScrapingOrchestrator(
            crawlers=crawlers,
            search_scrapers=search_scrapers,
            redis_client=redis_client,
            dedup=dedup,
            categories=categories,
        )
        orchestrator.set_trending_scraper_infra(rate_limiter, proxy_pool)

        marketplace_names = ", ".join(c.marketplace_id for c in crawlers)
        logger.info("Orchestrated mode — crawlers: %s", marketplace_names)
        logger.info(
            "Categories (%d): %s", len(categories), ", ".join(categories[:15]),
        )
        if len(categories) > 15:
            logger.info("  ... and %d more categories", len(categories) - 15)
        logger.info("Search scrapers: %d | Search terms will be auto-expanded", len(search_scrapers))

        # Estimate
        est_low = len(categories) * len(crawlers) * CATEGORY_MAX_PAGES * 20
        est_high = len(categories) * len(crawlers) * CATEGORY_MAX_PAGES * 50
        logger.info(
            "Estimated category listings per cycle: %d – %d (+ search + trending)",
            est_low, est_high,
        )

    # ── Category mode (legacy — categories only) ──
    elif SCRAPER_MODE == "category":
        categories = _build_category_list()
        crawlers = _build_crawlers(rate_limiter, proxy_pool)
        dedup = DedupFilter(redis_client)

        marketplace_names = ", ".join(c.marketplace_id for c in crawlers)
        logger.info("Category crawlers: %s", marketplace_names)
        logger.info(
            "Categories (%d): %s (max %d pages each)",
            len(categories), ", ".join(categories), CATEGORY_MAX_PAGES,
        )
        logger.info("Smart expansion: %s (multiplier: %.1fx)", SMART_EXPANSION, EXPANSION_MULTIPLIER)

        est_low = len(categories) * len(crawlers) * CATEGORY_MAX_PAGES * 20
        est_high = len(categories) * len(crawlers) * CATEGORY_MAX_PAGES * 50
        logger.info("Estimated listings per cycle: %d - %d", est_low, est_high)
    else:
        scrapers = _build_search_scrapers(rate_limiter, proxy_pool)
        marketplace_names = ", ".join(s.marketplace_id for s in scrapers)
        logger.info("Search scrapers: %s", marketplace_names)

    # Graceful shutdown
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Main loop
    _zero_result_streak = 0
    try:
        while not stop_event.is_set():
            if SCRAPER_MODE == "orchestrated":
                total = await orchestrator.run_cycle()
            elif SCRAPER_MODE == "category":
                dedup.reset()
                total = await run_category_cycle(crawlers, redis_client, categories, dedup)
            else:
                total = await run_search_cycle(scrapers, redis_client)

            if total == 0:
                _zero_result_streak += 1
                logger.error(
                    "SCRAPER ALERT: 0 listings in cycle (%d consecutive zero-result cycles)",
                    _zero_result_streak,
                )
                if _zero_result_streak >= 3:
                    logger.critical(
                        "SCRAPER CRITICAL: %d consecutive zero-result cycles — "
                        "all scrapers may be blocked or broken",
                        _zero_result_streak,
                    )
            else:
                if _zero_result_streak > 0:
                    logger.info("Scraper recovered after %d zero-result cycles", _zero_result_streak)
                _zero_result_streak = 0

            logger.info(
                "Cycle complete: %d listings published to '%s'", total, STREAM_KEY
            )

            # Log smart expansion stats (category mode)
            if SCRAPER_MODE == "category" and _category_stats:
                top = sorted(
                    _category_stats.items(),
                    key=lambda x: x[1].get("avg_listings_per_page", 0),
                    reverse=True,
                )[:5]
                logger.info("Top categories by yield: %s", [
                    f"{slug}: {s.get('avg_listings_per_page', 0):.1f}/page"
                    for slug, s in top
                ])

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=SCRAPE_INTERVAL)
            except asyncio.TimeoutError:
                pass
    finally:
        try:
            from .browser import close_shared_browser
            await close_shared_browser()
        except Exception:
            pass
        await redis_client.aclose()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
