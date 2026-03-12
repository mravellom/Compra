"""
Scraper entrypoint. Supports two modes:

  SCRAPER_MODE=search   (default) — keyword search, original behavior
  SCRAPER_MODE=category — category crawling with pagination
"""
import asyncio
import logging
import os
import signal
import sys

from dotenv import load_dotenv
load_dotenv()

import redis.asyncio as redis

from .amazon_scraper import AmazonScraper
from .amazon_us_scraper import AmazonUSScraper
from .aliexpress_scraper import AliExpressScraper
from .ebay_scraper import EbayScraper
from .mercadolibre_scraper import MercadoLibreScraper
from .amazon_category_crawler import AmazonCategoryCrawler
from .ml_category_crawler import MLCategoryCrawler
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
SCRAPER_MODE = os.getenv("SCRAPER_MODE", "search")  # "search" or "category"

# Search mode config
SEARCH_TERMS = os.getenv("SEARCH_TERMS", "sony wh-1000xm4,airpods pro,nintendo switch")
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "20"))

# Category mode config
CATEGORIES = os.getenv("CATEGORIES", "electronica,videojuegos,celulares")
CATEGORY_MAX_PAGES = int(os.getenv("CATEGORY_MAX_PAGES", "3"))


async def publish_to_stream(redis_client: redis.Redis, listing: RawListing) -> str:
    msg_id = await redis_client.xadd(STREAM_KEY, listing.to_stream_dict())
    return msg_id


async def publish_batch(redis_client: redis.Redis, listings: list[RawListing]) -> int:
    """Publish a batch of listings to Redis. Returns count published."""
    count = 0
    for listing in listings:
        try:
            await publish_to_stream(redis_client, listing)
            count += 1
        except Exception:
            logger.error("Failed to publish listing: %s", listing.title[:40], exc_info=True)
    return count


# ── Search mode ───────────────────────────────────────────
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


# ── Category mode ─────────────────────────────────────────
async def run_category_cycle(
    crawlers: list, redis_client: redis.Redis, categories: list[str]
) -> int:
    total = 0

    for category_slug in categories:
        for crawler in crawlers:
            logger.info(
                "Crawling category '%s' on %s (max %d pages)...",
                category_slug, crawler.marketplace_id, CATEGORY_MAX_PAGES,
            )
            try:
                listings = await crawler.crawl_category(
                    category_slug, max_pages=CATEGORY_MAX_PAGES
                )
            except Exception:
                logger.error(
                    "Error crawling '%s' on %s",
                    category_slug, crawler.marketplace_id, exc_info=True,
                )
                continue

            published = await publish_batch(redis_client, listings)
            total += published
            logger.info(
                "[%s] '%s': %d scraped, %d published",
                crawler.marketplace_id, category_slug, len(listings), published,
            )

    return total


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

    # Shared rate limiter and proxy pool
    rate_limiter = RateLimiter(requests_per_second=0.5, burst=2)
    proxy_pool = ProxyPool()  # Loads from PROXY_URLS env var

    if proxy_pool.has_proxies:
        logger.info("Proxy pool initialized with %d proxies", proxy_pool.size)
    else:
        logger.info("No proxies configured (set PROXY_URLS env var for proxy rotation)")

    # Build scraper/crawler list based on mode
    if SCRAPER_MODE == "category":
        categories = [c.strip() for c in CATEGORIES.split(",") if c.strip()]
        logger.info("Categories: %s (max %d pages each)", categories, CATEGORY_MAX_PAGES)

        crawlers = [
            MLCategoryCrawler("mercadolibre_ar", rate_limiter=rate_limiter, proxy_pool=proxy_pool),
            MLCategoryCrawler("mercadolibre_mx", rate_limiter=rate_limiter, proxy_pool=proxy_pool),
            AmazonCategoryCrawler(rate_limiter=rate_limiter, proxy_pool=proxy_pool),
        ]
        marketplace_names = ", ".join(c.marketplace_id for c in crawlers)
        logger.info("Category crawlers: %s", marketplace_names)
    else:
        scrapers = [
            MercadoLibreScraper("mercadolibre_ar", rate_limiter=rate_limiter, proxy_pool=proxy_pool),
            MercadoLibreScraper("mercadolibre_mx", rate_limiter=rate_limiter, proxy_pool=proxy_pool),
            MercadoLibreScraper("mercadolibre_cl", rate_limiter=rate_limiter, proxy_pool=proxy_pool),
            MercadoLibreScraper("mercadolibre_co", rate_limiter=rate_limiter, proxy_pool=proxy_pool),
            AmazonScraper(rate_limiter=rate_limiter, proxy_pool=proxy_pool),
            AmazonUSScraper(rate_limiter=rate_limiter, proxy_pool=proxy_pool),
            AliExpressScraper(rate_limiter=rate_limiter, proxy_pool=proxy_pool),
            EbayScraper(),
        ]
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
    try:
        while not stop_event.is_set():
            if SCRAPER_MODE == "category":
                total = await run_category_cycle(crawlers, redis_client, categories)
            else:
                total = await run_search_cycle(scrapers, redis_client)

            logger.info(
                "Cycle complete: %d listings published to '%s'", total, STREAM_KEY
            )

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=SCRAPE_INTERVAL)
            except asyncio.TimeoutError:
                pass
    finally:
        # Close shared Playwright browser if it was used
        try:
            from .browser import close_shared_browser
            await close_shared_browser()
        except Exception:
            pass
        await redis_client.aclose()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
