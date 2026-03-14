"""
High-throughput processor pipeline v5.

Architecture:
    Redis Reader (prefetch 256/read)
        → asyncio.Queue (buffer, maxsize=1024)
        → Batch Collector (256-1024 items, adaptive)
        → Light validation (pure, no IO)
        → Batch Embedding (thread pool, numpy)
        → Batch Resolve (in-memory numpy index — ZERO DB queries)
        → Write Buffer (accumulates rows, adaptive 256→1024)
        → Parallel Flush:
            ├─ UPSERT listings  (UNNEST, write_pool, sync_commit=off)
            └─ COPY history     (COPY protocol, copy_pool, sync_commit=off)
        → Deferred Median Refresh (every 5 min, background)
        → Batch ACK

Target: >15000 listings/sec.

Key optimizations over v4:
    1. COPY protocol for price_history (5-10x faster than INSERT).
    2. Parallel flush: UPSERT and COPY run concurrently via asyncio.gather.
    3. synchronous_commit=off on write/copy pools (~3x faster commits).
    4. Deferred median updates (removed UPDATE from hot path, runs every 5 min).
    5. Adaptive batch sizing (scales 256→1024 based on flush latency).
    6. Dedicated copy_pool prevents COPY streams from blocking UPSERTs.
"""
import asyncio
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import redis.asyncio as redis

from scraper.price_parser import validate_price
from api.currency import get_rates, to_usd

from .db import close_pool, get_pool, get_copy_pool, get_read_pool
from .embeddings import generate_embeddings_batch, DEVICE, EMBEDDING_BATCH_SIZE
from .normalizer import normalize_title
from .price_anomaly import detect_price_anomaly
from .product_index import get_index
from .resolver import batch_resolve, resolver_metrics, _get_fx_rates
from .write_buffer import get_write_buffer, ListingRow, write_metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM_KEY = "raw_listings_queue"
DEAD_LETTER_KEY = "dead_letter_queue"
GROUP_NAME = "processor_group"
CONSUMER_NAME = os.getenv("CONSUMER_NAME", "processor-1")

# Pipeline tuning
PREFETCH_SIZE = int(os.getenv("PREFETCH_SIZE", "1024"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "256"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
BACKPRESSURE_THRESHOLD = int(os.getenv("BACKPRESSURE_THRESHOLD", "50000"))
METRICS_INTERVAL = int(os.getenv("METRICS_INTERVAL", "10"))

# ── USD-normalized price validation ─────────────────────────
_MIN_PRICE_USD = 1.0
_MAX_PRICE_USD = 20_000.0
_MEDIAN_DEVIATION_MAX = 0.80

# ── Price anomaly cache ─────────────────────────────────────
_ANOMALY_CACHE_SIZE = int(os.getenv("ANOMALY_CACHE_SIZE", "8192"))
_ANOMALY_CACHE_TTL = 900
_anomaly_cache: dict[str, tuple[float, list[float]]] = {}


def _anomaly_cache_get(url: str) -> list[float] | None:
    entry = _anomaly_cache.get(url)
    if entry is None:
        return None
    ts, prices = entry
    if time.monotonic() - ts > _ANOMALY_CACHE_TTL:
        del _anomaly_cache[url]
        return None
    return prices


def _anomaly_cache_put(url: str, prices: list[float]) -> None:
    if len(_anomaly_cache) >= _ANOMALY_CACHE_SIZE:
        sorted_keys = sorted(_anomaly_cache, key=lambda k: _anomaly_cache[k][0])
        for k in sorted_keys[: _ANOMALY_CACHE_SIZE // 4]:
            del _anomaly_cache[k]
    _anomaly_cache[url] = (time.monotonic(), prices)


# ── Metrics ──────────────────────────────────────────────────
@dataclass
class PipelineMetrics:
    processed: int = 0
    skipped: int = 0
    errors: int = 0
    batches: int = 0
    embed_time: float = 0.0
    resolve_time: float = 0.0
    enqueue_time: float = 0.0
    _start_time: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start_time

    @property
    def throughput(self) -> float:
        e = self.elapsed
        return self.processed / e if e > 0 else 0.0

    @property
    def avg_embed_ms(self) -> float:
        return (self.embed_time / self.batches * 1000) if self.batches > 0 else 0.0

    @property
    def avg_resolve_ms(self) -> float:
        return (self.resolve_time / self.batches * 1000) if self.batches > 0 else 0.0

    @property
    def avg_enqueue_ms(self) -> float:
        return (self.enqueue_time / self.batches * 1000) if self.batches > 0 else 0.0

    def log(self, queue_depth: int = 0):
        logger.info(
            "PIPELINE | throughput=%.0f/s | processed=%d | skipped=%d | errors=%d | "
            "batches=%d | avg_embed=%.1fms | avg_resolve=%.1fms | avg_enqueue=%.1fms | "
            "queue=%d | elapsed=%.0fs | index_size=%d",
            self.throughput, self.processed, self.skipped, self.errors,
            self.batches, self.avg_embed_ms, self.avg_resolve_ms, self.avg_enqueue_ms,
            queue_depth, self.elapsed, get_index().size,
        )
        resolver_metrics.log()
        write_metrics.log()


metrics = PipelineMetrics()


# ── Price validation (in-memory, no DB) ──────────────────────
def validate_price_usd_sync(
    price_usd: float,
    master_product_id: int,
) -> bool:
    if price_usd < _MIN_PRICE_USD or price_usd > _MAX_PRICE_USD:
        return False

    product = get_index().get_product(master_product_id)
    if not product:
        return True

    if product.median_price_usd <= 0 or product.listing_price_count < 3:
        return True

    deviation = abs(price_usd - product.median_price_usd) / product.median_price_usd
    return deviation <= _MEDIAN_DEVIATION_MAX


# ── Message parsing (NO DB queries — fast path) ──────────────
@dataclass
class ParsedListing:
    msg_id: str
    title: str
    normalized_title: str
    price: float
    currency: str
    url: str
    marketplace_id: str
    image_url: str | None
    scraped_at: datetime
    condition: str
    seller_name: str | None
    seller_rating: float | None
    reviews_count: int
    sales_count: int
    stock_available: int | None
    is_free_shipping: bool
    shipping_price: float | None


def parse_message(msg_id: str, data: dict) -> ParsedListing | None:
    """Parse and validate. Pure function — no DB, no async."""
    title = data.get("title", "")
    url = data.get("url", "")
    if not title or not url:
        return None

    price = float(data.get("price", "0"))
    currency = data.get("currency", "USD")
    marketplace_id = data.get("marketplace_id", "")

    if not validate_price(price, currency, marketplace_id, title):
        return None

    cached_history = _anomaly_cache_get(url)
    if cached_history is not None:
        if detect_price_anomaly(price, cached_history, currency=currency):
            return None

    scraped_at_str = data.get("scraped_at")
    if scraped_at_str:
        scraped_at = datetime.fromisoformat(scraped_at_str)
        if scraped_at.tzinfo is None:
            scraped_at = scraped_at.replace(tzinfo=timezone.utc)
    else:
        scraped_at = datetime.now(timezone.utc)

    seller_rating_str = data.get("seller_rating", "")
    stock_str = data.get("stock_available", "")
    shipping_str = data.get("shipping_price", "")

    return ParsedListing(
        msg_id=msg_id,
        title=title,
        normalized_title=normalize_title(title),
        price=price,
        currency=currency,
        url=url,
        marketplace_id=marketplace_id,
        image_url=data.get("image_url") or None,
        scraped_at=scraped_at,
        condition=data.get("condition", "new"),
        seller_name=data.get("seller_name") or None,
        seller_rating=float(seller_rating_str) if seller_rating_str else None,
        reviews_count=int(data.get("reviews_count", "0") or "0"),
        sales_count=int(data.get("sales_count", "0") or "0"),
        stock_available=int(stock_str) if stock_str else None,
        is_free_shipping=data.get("is_free_shipping", "False").lower() == "true",
        shipping_price=float(shipping_str) if shipping_str else None,
    )


# ── Pipeline stages ─────────────────────────────────────────

async def reader_stage(
    redis_client: redis.Redis,
    buffer: asyncio.Queue,
    stop_event: asyncio.Event,
):
    """Stage 1: Prefetch messages from Redis into an async buffer.

    First drains any pending (previously delivered but unACKed) messages
    by reading with ID '0', then switches to '>' for new messages.
    """
    read_batch = min(PREFETCH_SIZE, 256)

    # Phase 1: drain pending messages (ID '0')
    logger.info("Reader: draining pending messages...")
    pending_count = 0
    while not stop_event.is_set():
        try:
            messages = await redis_client.xreadgroup(
                groupname=GROUP_NAME,
                consumername=CONSUMER_NAME,
                streams={STREAM_KEY: "0"},
                count=read_batch,
                block=100,
            )
        except Exception:
            logger.error("Reader error (pending)", exc_info=True)
            await asyncio.sleep(1)
            continue

        if not messages:
            break

        found_any = False
        for _stream, entries in messages:
            if not entries:
                continue
            found_any = True
            for msg_id, data in entries:
                if not data:
                    # Already ACKed but still in PEL — just ACK again
                    await redis_client.xack(STREAM_KEY, GROUP_NAME, msg_id)
                    continue
                await buffer.put((msg_id, data))
                pending_count += 1

        if not found_any:
            break

    logger.info("Reader: drained %d pending messages, switching to new messages", pending_count)

    # Phase 2: read new messages (ID '>')
    while not stop_event.is_set():
        try:
            messages = await redis_client.xreadgroup(
                groupname=GROUP_NAME,
                consumername=CONSUMER_NAME,
                streams={STREAM_KEY: ">"},
                count=read_batch,
                block=1000,
            )
        except Exception:
            logger.error("Reader error", exc_info=True)
            await asyncio.sleep(1)
            continue

        if not messages:
            continue

        for _stream, entries in messages:
            for msg_id, data in entries:
                await buffer.put((msg_id, data))

    await buffer.put(None)


async def batch_process_stage(
    redis_client: redis.Redis,
    buffer: asyncio.Queue,
    stop_event: asyncio.Event,
):
    """Stage 2: Collect → parse → embed → batch_resolve → enqueue to write buffer."""
    loop = asyncio.get_running_loop()
    wb = get_write_buffer()

    while not stop_event.is_set():
        # ── Collect a batch ──────────────────────────────
        raw_batch: list[tuple[str, dict]] = []
        try:
            item = await asyncio.wait_for(buffer.get(), timeout=2.0)
            if item is None:
                break
            raw_batch.append(item)
        except asyncio.TimeoutError:
            continue

        # Wait briefly for more items to accumulate a full batch
        deadline = time.monotonic() + 0.1  # 100ms max wait
        while len(raw_batch) < BATCH_SIZE:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                item = await asyncio.wait_for(buffer.get(), timeout=remaining)
                if item is None:
                    break
                raw_batch.append(item)
            except asyncio.TimeoutError:
                break

        if not raw_batch:
            continue

        # ── Parse & validate (NO DB queries) ─────────────
        parsed: list[ParsedListing] = []
        ack_ids: list[str] = []

        for msg_id, data in raw_batch:
            ack_ids.append(msg_id)
            try:
                listing = parse_message(msg_id, data)
                if listing:
                    parsed.append(listing)
                else:
                    metrics.skipped += 1
            except Exception:
                logger.error("Parse error for %s", msg_id, exc_info=True)
                metrics.errors += 1

        if not parsed:
            if ack_ids:
                await redis_client.xack(STREAM_KEY, GROUP_NAME, *ack_ids)
            continue

        # ── Batch embedding (thread pool) ────────────────
        titles = [p.normalized_title for p in parsed]
        t0 = time.monotonic()
        embeddings = await loop.run_in_executor(
            None, generate_embeddings_batch, titles
        )
        metrics.embed_time += time.monotonic() - t0

        # ── Batch resolve (in-memory index) ──────────────
        t0 = time.monotonic()
        match_results = await batch_resolve(
            titles=[p.title for p in parsed],
            embeddings=embeddings,
            prices=[p.price for p in parsed],
            currencies=[p.currency for p in parsed],
        )
        metrics.resolve_time += time.monotonic() - t0
        metrics.batches += 1

        # ── Filter + enqueue to write buffer ─────────────
        t0 = time.monotonic()
        rates = await _get_fx_rates()
        enqueue_tasks = []

        for listing, match in zip(parsed, match_results):
            if match.master_product_id < 0:
                metrics.errors += 1
                continue

            price_usd = to_usd(listing.price, listing.currency, rates)

            # USD price validation (in-memory, no DB)
            if not match.is_new:
                if not validate_price_usd_sync(price_usd, match.master_product_id):
                    metrics.skipped += 1
                    continue

            row = ListingRow(
                master_product_id=match.master_product_id,
                title=listing.title,
                normalized_title=listing.normalized_title,
                price=listing.price,
                currency=listing.currency,
                url=listing.url,
                marketplace_id=listing.marketplace_id,
                image_url=listing.image_url,
                similarity=match.similarity,
                scraped_at=listing.scraped_at,
                condition=listing.condition,
                seller_name=listing.seller_name,
                seller_rating=listing.seller_rating,
                reviews_count=listing.reviews_count,
                sales_count=listing.sales_count,
                stock_available=listing.stock_available,
                is_free_shipping=listing.is_free_shipping,
                shipping_price=listing.shipping_price,
                price_usd=price_usd,
            )

            enqueue_tasks.append(wb.enqueue(row))

        # Enqueue all rows concurrently (they'll wait for the flush)
        if enqueue_tasks:
            results = await asyncio.gather(*enqueue_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    metrics.errors += 1
                    logger.error("Write buffer error", exc_info=result)
                else:
                    metrics.processed += 1

                    # Update anomaly cache
                    # (we access parsed items corresponding to successfully enqueued rows)

        # Update anomaly cache for all processed items
        for listing, match in zip(parsed, match_results):
            if match.master_product_id >= 0:
                cached = _anomaly_cache_get(listing.url) or []
                cached = [listing.price] + cached[:19]
                _anomaly_cache_put(listing.url, cached)

        metrics.enqueue_time += time.monotonic() - t0

        # ── Ack all messages ─────────────────────────────
        if ack_ids:
            await redis_client.xack(STREAM_KEY, GROUP_NAME, *ack_ids)


async def write_flush_stage(stop_event: asyncio.Event):
    """Stage 3: Write buffer flush loop (runs as background task)."""
    wb = get_write_buffer()
    await wb.flush_loop(stop_event)
    await wb.flush_remaining()


async def median_refresh_stage(stop_event: asyncio.Event):
    """Stage 4: Periodically flush deferred median updates to DB."""
    wb = get_write_buffer()
    while not stop_event.is_set():
        try:
            if wb.median_acc.needs_flush():
                await wb.median_acc.flush()
        except Exception:
            logger.error("Median refresh error", exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
            break
        except asyncio.TimeoutError:
            pass


async def index_refresh_stage(stop_event: asyncio.Event):
    """Background stage: periodically refresh the in-memory product index."""
    index = get_index()
    while not stop_event.is_set():
        try:
            if index.needs_refresh():
                pool = await get_read_pool()
                await index.load(pool)
        except Exception:
            logger.error("Index refresh error", exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=30)
            break
        except asyncio.TimeoutError:
            pass


async def anomaly_cache_warmer(stop_event: asyncio.Event):
    """Background stage: warm the anomaly cache from price_history."""
    while not stop_event.is_set():
        try:
            pool = await get_read_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT listing_url, array_agg(price::float ORDER BY recorded_at DESC) AS prices
                    FROM (
                        SELECT listing_url, price, recorded_at,
                               ROW_NUMBER() OVER (PARTITION BY listing_url ORDER BY recorded_at DESC) AS rn
                        FROM price_history
                        WHERE recorded_at > now() - interval '24 hours'
                    ) sub
                    WHERE rn <= 20
                    GROUP BY listing_url
                    """
                )
                for row in rows:
                    _anomaly_cache_put(row["listing_url"], row["prices"])

                if rows:
                    logger.info("Anomaly cache warmed: %d URLs loaded", len(rows))
        except Exception:
            logger.error("Anomaly cache warmer error", exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=900)
            break
        except asyncio.TimeoutError:
            pass


async def metrics_stage(
    redis_client: redis.Redis,
    stop_event: asyncio.Event,
):
    """Periodically log throughput and queue metrics."""
    while not stop_event.is_set():
        await asyncio.sleep(METRICS_INTERVAL)
        try:
            info = await redis_client.xinfo_stream(STREAM_KEY)
            queue_depth = info.get("length", 0)
        except Exception:
            queue_depth = -1
        metrics.log(queue_depth)


async def backpressure_stage(
    redis_client: redis.Redis,
    stop_event: asyncio.Event,
):
    """Monitor queue depth and publish backpressure signals."""
    if BACKPRESSURE_THRESHOLD <= 0:
        return

    was_throttled = False
    while not stop_event.is_set():
        await asyncio.sleep(30)
        try:
            info = await redis_client.xinfo_stream(STREAM_KEY)
            depth = info.get("length", 0)

            if depth > BACKPRESSURE_THRESHOLD and not was_throttled:
                logger.warning("BACKPRESSURE: queue depth %d > threshold %d", depth, BACKPRESSURE_THRESHOLD)
                await redis_client.publish("backpressure", "throttle")
                was_throttled = True
            elif depth < BACKPRESSURE_THRESHOLD * 0.5 and was_throttled:
                logger.info("BACKPRESSURE: queue depth %d recovered", depth)
                await redis_client.publish("backpressure", "resume")
                was_throttled = False
        except Exception:
            logger.error("Backpressure check error", exc_info=True)


# ── Consumer group setup ─────────────────────────────────────

async def ensure_consumer_group(r: redis.Redis) -> None:
    try:
        await r.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
        logger.info("Created consumer group '%s'", GROUP_NAME)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def drain_dead_letters(r: redis.Redis) -> None:
    try:
        pending_info = await r.xpending_range(
            STREAM_KEY, GROUP_NAME, "-", "+", 100, CONSUMER_NAME,
        )
        dlq_count = 0
        for entry in pending_info:
            if entry.get("times_delivered", 0) > MAX_RETRIES:
                msg_id = entry["message_id"]
                await r.xadd(DEAD_LETTER_KEY, {
                    "original_id": msg_id,
                    "consumer": CONSUMER_NAME,
                    "retries": str(entry.get("times_delivered", 0)),
                })
                await r.xack(STREAM_KEY, GROUP_NAME, msg_id)
                dlq_count += 1
        if dlq_count:
            logger.warning("Moved %d messages to dead letter queue", dlq_count)
    except Exception:
        logger.error("Error checking pending retries", exc_info=True)


async def process_pending(r: redis.Redis) -> None:
    try:
        pending_messages = await r.xreadgroup(
            groupname=GROUP_NAME,
            consumername=CONSUMER_NAME,
            streams={STREAM_KEY: "0"},
            count=100,
        )
        if pending_messages:
            from .consumer import process_message
            for _stream, entries in pending_messages:
                for msg_id, data in entries:
                    if not data:
                        continue
                    try:
                        await process_message(msg_id, data)
                        await r.xack(STREAM_KEY, GROUP_NAME, msg_id)
                    except Exception:
                        logger.error("Error processing pending message %s", msg_id, exc_info=True)
            logger.info("Processed pending messages")
    except Exception:
        logger.error("Error processing pending messages", exc_info=True)


# ── Main entry point ─────────────────────────────────────────

async def main() -> None:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await redis_client.ping()
        logger.info("Connected to Redis")
    except redis.ConnectionError:
        logger.error("Cannot connect to Redis at %s", REDIS_URL)
        sys.exit(1)

    await ensure_consumer_group(redis_client)

    # ── Load in-memory product index ─────────────────────
    index = get_index()
    read_pool = await get_read_pool()
    await index.load(read_pool)
    logger.info("Product index ready: %d products", index.size)

    # Graceful shutdown
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info(
        "Pipeline v5 starting | consumer=%s | device=%s | batch_size=%d | "
        "prefetch=%d | index_size=%d | sync_commit=%s",
        CONSUMER_NAME, DEVICE, BATCH_SIZE, PREFETCH_SIZE, index.size,
        os.getenv("DB_SYNC_COMMIT", "off"),
    )

    # Handle dead letters from previous runs
    await drain_dead_letters(redis_client)

    buffer: asyncio.Queue = asyncio.Queue(maxsize=PREFETCH_SIZE)

    try:
        await asyncio.gather(
            reader_stage(redis_client, buffer, stop_event),
            batch_process_stage(redis_client, buffer, stop_event),
            write_flush_stage(stop_event),
            median_refresh_stage(stop_event),
            index_refresh_stage(stop_event),
            anomaly_cache_warmer(stop_event),
            metrics_stage(redis_client, stop_event),
            backpressure_stage(redis_client, stop_event),
        )
    finally:
        # Flush remaining writes before closing pools
        wb = get_write_buffer()
        await wb.flush_remaining()
        await redis_client.aclose()
        await close_pool()
        metrics.log()
        logger.info("Pipeline shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
