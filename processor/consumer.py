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

from .db import close_pool
from .normalizer import normalize_title
from .resolver import resolve_product, save_listing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM_KEY = "raw_listings_queue"
GROUP_NAME = "processor_group"
CONSUMER_NAME = os.getenv("CONSUMER_NAME", "processor-1")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10"))


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
    scraped_at = datetime.fromisoformat(scraped_at_str) if scraped_at_str else datetime.now(timezone.utc)

    if not title or not url:
        logger.warning("Skipping message %s: missing title or url", msg_id)
        return

    price = float(price_str)
    normalized = normalize_title(title)

    # Resolver producto (match o crear nuevo)
    match = await resolve_product(title)

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
