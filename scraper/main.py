import asyncio
import logging
import os
import signal
import sys

import redis.asyncio as redis

from .amazon_scraper import AmazonScraper
from .ebay_scraper import EbayScraper
from .mercadolibre_scraper import MercadoLibreScraper
from .schemas import RawListing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM_KEY = "raw_listings_queue"
SEARCH_TERMS = os.getenv("SEARCH_TERMS", "sony wh-1000xm4,airpods pro,nintendo switch")
SCRAPE_INTERVAL = int(os.getenv("SCRAPE_INTERVAL", "300"))  # segundos entre ciclos
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "20"))


async def publish_to_stream(redis_client: redis.Redis, listing: RawListing) -> str:
    """Publica un RawListing en el Redis Stream. Retorna el message ID."""
    msg_id = await redis_client.xadd(STREAM_KEY, listing.to_stream_dict())
    return msg_id


async def run_scrape_cycle(scrapers: list, redis_client: redis.Redis) -> int:
    """Ejecuta un ciclo completo de scraping para todos los search terms."""
    terms = [t.strip() for t in SEARCH_TERMS.split(",") if t.strip()]
    total = 0

    for term in terms:
        for scraper in scrapers:
            logger.info("Scraping '%s' on %s...", term, scraper.marketplace_id)
            listings = await scraper.scrape(term, max_results=MAX_RESULTS)

            for listing in listings:
                msg_id = await publish_to_stream(redis_client, listing)
                logger.debug("Published %s -> %s", listing.title[:40], msg_id)

            total += len(listings)
            # Pausa entre busquedas para no saturar
            await asyncio.sleep(2)

    return total


async def main() -> None:
    # Conexion a Redis
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await redis_client.ping()
        logger.info("Connected to Redis at %s", REDIS_URL)
    except redis.ConnectionError:
        logger.error("Cannot connect to Redis at %s", REDIS_URL)
        sys.exit(1)

    # Iniciar scrapers
    scrapers = [
        MercadoLibreScraper("mercadolibre_ar"),
        MercadoLibreScraper("mercadolibre_mx"),
        AmazonScraper(),
        EbayScraper(),
    ]
    marketplace_names = ", ".join(s.marketplace_id for s in scrapers)
    logger.info("Scrapers started: %s", marketplace_names)

    # Graceful shutdown
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Loop principal
    try:
        while not stop_event.is_set():
            total = await run_scrape_cycle(scrapers, redis_client)
            logger.info("Cycle complete: %d listings published to '%s'", total, STREAM_KEY)

            # Esperar el intervalo o hasta que llegue señal de stop
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=SCRAPE_INTERVAL)
            except asyncio.TimeoutError:
                pass
    finally:
        await redis_client.aclose()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
