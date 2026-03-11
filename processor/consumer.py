"""
Consumer que lee del Redis Stream raw_listings_queue,
normaliza, resuelve el producto y guarda en PostgreSQL.
"""
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone

import redis.asyncio as redis

from scraper.price_parser import validate_price

from api.currency import get_rates, to_usd

from .db import close_pool, get_pool
from .normalizer import normalize_title
from .price_anomaly import detect_price_anomaly
from .resolver import resolve_product, save_listing


# ── USD-normalized price validation ─────────────────────────
_MIN_PRICE_USD = 1.0
_MAX_PRICE_USD = 20_000.0
_MEDIAN_DEVIATION_MAX = 0.80  # reject if >80% away from product median


async def validate_price_usd(
    price: float,
    currency: str,
    master_product_id: int,
) -> bool:
    """Reject listings whose USD price is absurd or deviates >80% from product median.

    Returns True if the price is acceptable.
    """
    rates = await get_rates()
    price_usd = to_usd(price, currency, rates)

    if price_usd < _MIN_PRICE_USD or price_usd > _MAX_PRICE_USD:
        logger.warning(
            "USD price out of bounds: %.2f %s (= $%.2f USD, bounds $%.0f–$%.0f)",
            price, currency, price_usd, _MIN_PRICE_USD, _MAX_PRICE_USD,
        )
        return False

    # Compare against existing listings for the same master product
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT price::float, currency FROM product_listings WHERE master_product_id = $1",
            master_product_id,
        )
    if len(rows) < 3:
        return True  # not enough data to judge

    existing_usd = [to_usd(r["price"], r["currency"], rates) for r in rows]
    import statistics
    median_usd = statistics.median(existing_usd)
    if median_usd <= 0:
        return True

    deviation = abs(price_usd - median_usd) / median_usd
    if deviation > _MEDIAN_DEVIATION_MAX:
        logger.warning(
            "USD price deviates %.0f%% from product median: $%.2f vs median $%.2f (master=%d)",
            deviation * 100, price_usd, median_usd, master_product_id,
        )
        return False

    return True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM_KEY = "raw_listings_queue"
DEAD_LETTER_KEY = "dead_letter_queue"
GROUP_NAME = "processor_group"
CONSUMER_NAME = os.getenv("CONSUMER_NAME", "processor-1")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))


async def ensure_consumer_group(r: redis.Redis) -> None:
    """Crea el consumer group si no existe."""
    try:
        await r.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
        logger.info("Created consumer group '%s'", GROUP_NAME)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def process_message(msg_id: str, data: dict) -> None:
    """Procesa un mensaje individual del stream."""
    title = data.get("title", "")
    price_str = data.get("price", "0")
    currency = data.get("currency", "USD")
    url = data.get("url", "")
    marketplace_id = data.get("marketplace_id", "")
    image_url = data.get("image_url") or None
    scraped_at_str = data.get("scraped_at")
    if scraped_at_str:
        scraped_at = datetime.fromisoformat(scraped_at_str)
        # Ensure timezone-aware (assume UTC if naive)
        if scraped_at.tzinfo is None:
            scraped_at = scraped_at.replace(tzinfo=timezone.utc)
    else:
        scraped_at = datetime.now(timezone.utc)

    if not title or not url:
        logger.warning("Skipping message %s: missing title or url", msg_id)
        return

    price = float(price_str)

    if not validate_price(price, currency, marketplace_id, title):
        logger.warning(
            "Price validation failed: %.2f %s for '%s'",
            price, currency, title[:50],
        )
        return

    # Price anomaly detection: compare against historical prices for this URL
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT price::float FROM price_history WHERE listing_url = $1 ORDER BY recorded_at DESC LIMIT 20",
            url,
        )
    historical_prices = [row["price"] for row in rows]
    if detect_price_anomaly(price, historical_prices, currency=currency):
        logger.warning("Skipping message %s: anomalous price %.2f %s for %s", msg_id, price, currency, url)
        return

    normalized = normalize_title(title)

    # Extract enhanced metadata
    condition = data.get("condition", "new")
    seller_name = data.get("seller_name") or None
    seller_rating_str = data.get("seller_rating", "")
    seller_rating = float(seller_rating_str) if seller_rating_str else None
    reviews_count = int(data.get("reviews_count", "0") or "0")
    sales_count = int(data.get("sales_count", "0") or "0")
    stock_str = data.get("stock_available", "")
    stock_available = int(stock_str) if stock_str else None
    is_free_shipping = data.get("is_free_shipping", "False").lower() == "true"
    shipping_str = data.get("shipping_price", "")
    shipping_price = float(shipping_str) if shipping_str else None

    # Resolver producto (match o crear nuevo, with price signal)
    match = await resolve_product(title, price=price, currency=currency)

    # USD-normalized price validation against product cluster
    if not match.is_new and not await validate_price_usd(price, currency, match.master_product_id):
        logger.warning(
            "Skipping message %s: USD price validation failed for master_product %d",
            msg_id, match.master_product_id,
        )
        return

    # Guardar listing vinculado
    listing_id = await save_listing(
        master_product_id=match.master_product_id,
        title=title,
        normalized_title=normalized,
        price=price,
        currency=currency,
        url=url,
        marketplace_id=marketplace_id,
        image_url=image_url,
        similarity=match.similarity,
        scraped_at=scraped_at,
        condition=condition,
        seller_name=seller_name,
        seller_rating=seller_rating,
        reviews_count=reviews_count,
        sales_count=sales_count,
        stock_available=stock_available,
        is_free_shipping=is_free_shipping,
        shipping_price=shipping_price,
    )

    action = "CREATED" if match.is_new else "MATCHED"
    logger.info(
        "[%s] listing_id=%d -> master=%d ('%s') sim=%.3f | %s $%.2f",
        action, listing_id, match.master_product_id,
        match.canonical_name[:40], match.similarity,
        marketplace_id, price,
    )


async def main() -> None:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await redis_client.ping()
        logger.info("Connected to Redis")
    except redis.ConnectionError:
        logger.error("Cannot connect to Redis at %s", REDIS_URL)
        sys.exit(1)

    await ensure_consumer_group(redis_client)

    # Graceful shutdown
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("Consumer '%s' listening on stream '%s'...", CONSUMER_NAME, STREAM_KEY)

    # Move messages that exceeded MAX_RETRIES to dead letter queue
    try:
        pending_info = await redis_client.xpending_range(
            STREAM_KEY, GROUP_NAME, "-", "+", 100, CONSUMER_NAME,
        )
        dlq_count = 0
        for entry in pending_info:
            if entry.get("times_delivered", 0) > MAX_RETRIES:
                msg_id = entry["message_id"]
                logger.error("Message %s exceeded %d retries, moving to DLQ", msg_id, MAX_RETRIES)
                await redis_client.xadd(DEAD_LETTER_KEY, {
                    "original_id": msg_id,
                    "consumer": CONSUMER_NAME,
                    "retries": str(entry.get("times_delivered", 0)),
                })
                await redis_client.xack(STREAM_KEY, GROUP_NAME, msg_id)
                dlq_count += 1
        if dlq_count:
            logger.warning("Moved %d messages to dead letter queue", dlq_count)
    except Exception:
        logger.error("Error checking pending retries", exc_info=True)

    # Procesar mensajes pending antes de leer nuevos
    try:
        pending_messages = await redis_client.xreadgroup(
            groupname=GROUP_NAME,
            consumername=CONSUMER_NAME,
            streams={STREAM_KEY: "0"},
            count=100,
        )
        if pending_messages:
            for _stream, entries in pending_messages:
                for msg_id, data in entries:
                    if not data:
                        continue
                    try:
                        await process_message(msg_id, data)
                        await redis_client.xack(STREAM_KEY, GROUP_NAME, msg_id)
                    except Exception:
                        logger.error("Error processing pending message %s", msg_id, exc_info=True)
            logger.info("Processed pending messages")
    except Exception:
        logger.error("Error processing pending messages", exc_info=True)

    try:
        while not stop_event.is_set():
            # Leer batch de mensajes
            messages = await redis_client.xreadgroup(
                groupname=GROUP_NAME,
                consumername=CONSUMER_NAME,
                streams={STREAM_KEY: ">"},
                count=BATCH_SIZE,
                block=2000,  # block 2 segundos esperando nuevos mensajes
            )

            if not messages:
                continue

            for _stream, entries in messages:
                for msg_id, data in entries:
                    try:
                        await process_message(msg_id, data)
                        await redis_client.xack(STREAM_KEY, GROUP_NAME, msg_id)
                    except Exception:
                        logger.error("Error processing message %s", msg_id, exc_info=True)
    finally:
        await redis_client.aclose()
        await close_pool()
        logger.info("Consumer shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
