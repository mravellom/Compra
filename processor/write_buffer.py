"""
Async write buffer v2 — COPY protocol, adaptive batching, deferred medians.

Architecture:
    pipeline batch_process_stage
          ↓ enqueue() (non-blocking)
    WriteBuffer._buffer (accumulates rows)
          ↓ flush every FLUSH_INTERVAL_MS or adaptive_flush_size rows
    ┌─────┴─────────────────┐
    │  asyncio.gather:      │
    │  ├─ UPSERT listings   │  (UNNEST, write_pool)
    │  └─ COPY history      │  (COPY protocol, copy_pool)
    └───────────────────────┘
    MedianAccumulator (deferred, flushes every 5 min)

Performance vs v1:
    v1: 1 transaction × 3 queries (UPSERT + INSERT + UPDATE) per flush
    v2: 2 parallel ops (UPSERT ‖ COPY) per flush + deferred median every 5 min
        COPY is 5-10x faster than INSERT for append-only tables.
        Parallel flush halves wall-clock time per flush.
        Deferred median eliminates UPDATE from hot path entirely.
"""
import asyncio
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .db import get_pool, get_copy_pool

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────
FLUSH_SIZE_MIN = int(os.getenv("WRITE_FLUSH_SIZE_MIN", "256"))
FLUSH_SIZE_MAX = int(os.getenv("WRITE_FLUSH_SIZE_MAX", "1024"))
FLUSH_INTERVAL_MS = int(os.getenv("WRITE_FLUSH_INTERVAL_MS", "75"))
MEDIAN_REFRESH_INTERVAL = int(os.getenv("MEDIAN_REFRESH_INTERVAL", "300"))  # 5 min

# Adaptive batching target latency (ms)
_TARGET_LATENCY_MS = float(os.getenv("WRITE_TARGET_LATENCY_MS", "25"))
_MAX_LATENCY_MS = float(os.getenv("WRITE_MAX_LATENCY_MS", "50"))


# ── Row types ────────────────────────────────────────────────
@dataclass
class ListingRow:
    """A single listing row ready for batch insert."""
    master_product_id: int
    title: str
    normalized_title: str
    price: float
    currency: str
    url: str
    marketplace_id: str
    image_url: str | None
    similarity: float
    scraped_at: datetime | str | None
    condition: str
    seller_name: str | None
    seller_rating: float | None
    reviews_count: int
    sales_count: int
    stock_available: int | None
    is_free_shipping: bool
    shipping_price: float | None
    price_usd: float


# ── Write metrics ────────────────────────────────────────────
@dataclass
class WriteMetrics:
    flushes: int = 0
    rows_written: int = 0
    total_flush_time: float = 0.0
    total_upsert_time: float = 0.0
    total_copy_time: float = 0.0
    total_median_time: float = 0.0
    median_refreshes: int = 0
    errors: int = 0
    current_batch_size: int = FLUSH_SIZE_MIN
    _start_time: float = field(default_factory=time.monotonic)

    @property
    def avg_flush_ms(self) -> float:
        return (self.total_flush_time / self.flushes * 1000) if self.flushes > 0 else 0.0

    @property
    def avg_rows_per_flush(self) -> float:
        return self.rows_written / self.flushes if self.flushes > 0 else 0.0

    @property
    def writes_per_sec(self) -> float:
        elapsed = time.monotonic() - self._start_time
        return self.rows_written / elapsed if elapsed > 0 else 0.0

    def log(self):
        logger.info(
            "WRITER | flushes=%d | rows=%d | avg_batch=%.0f | adaptive_size=%d | "
            "avg_flush=%.1fms | writes/s=%.0f | upsert=%.1fms | copy=%.1fms | "
            "median_refreshes=%d | errors=%d",
            self.flushes, self.rows_written, self.avg_rows_per_flush,
            self.current_batch_size, self.avg_flush_ms, self.writes_per_sec,
            (self.total_upsert_time / self.flushes * 1000) if self.flushes else 0,
            (self.total_copy_time / self.flushes * 1000) if self.flushes else 0,
            self.median_refreshes, self.errors,
        )


write_metrics = WriteMetrics()


# ── Median accumulator (deferred updates) ────────────────────

class MedianAccumulator:
    """Accumulates price data and flushes median updates periodically.

    Instead of updating master_products.median_price_usd on every write flush,
    this accumulates (product_id → list[price_usd]) and flushes once every
    MEDIAN_REFRESH_INTERVAL seconds. This removes the UPDATE query from the
    hot write path entirely.
    """

    def __init__(self):
        self._data: dict[int, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._last_flush = time.monotonic()

    async def add(self, product_id: int, price_usd: float) -> None:
        async with self._lock:
            self._data[product_id].append(price_usd)

    async def add_batch(self, batch: list[ListingRow]) -> None:
        async with self._lock:
            for row in batch:
                self._data[row.master_product_id].append(row.price_usd)

    def needs_flush(self) -> bool:
        return (time.monotonic() - self._last_flush) > MEDIAN_REFRESH_INTERVAL

    async def flush(self) -> int:
        """Flush accumulated median updates to DB. Returns number of products updated."""
        async with self._lock:
            if not self._data:
                self._last_flush = time.monotonic()
                return 0
            data = self._data
            self._data = defaultdict(list)

        t0 = time.monotonic()
        up_ids = []
        up_counts = []
        up_prices = []
        for pid, prices in data.items():
            up_ids.append(pid)
            up_counts.append(len(prices))
            # Use median of accumulated prices for better accuracy
            prices.sort()
            up_prices.append(prices[len(prices) // 2])

        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    WITH locked AS (
                        SELECT mp.id
                        FROM master_products mp
                        WHERE mp.id = ANY($1::bigint[])
                        FOR UPDATE
                    )
                    UPDATE master_products mp
                    SET listing_price_count = COALESCE(mp.listing_price_count, 0) + batch.cnt,
                        median_price_usd = CASE
                            WHEN COALESCE(mp.listing_price_count, 0) = 0 THEN batch.price
                            WHEN batch.price > COALESCE(mp.median_price_usd, 0)
                                THEN COALESCE(mp.median_price_usd, 0) +
                                     ((batch.price - COALESCE(mp.median_price_usd, 0)) * batch.cnt /
                                      (COALESCE(mp.listing_price_count, 0) + batch.cnt))
                            WHEN batch.price < COALESCE(mp.median_price_usd, 0)
                                THEN COALESCE(mp.median_price_usd, 0) -
                                     ((COALESCE(mp.median_price_usd, 0) - batch.price) * batch.cnt /
                                      (COALESCE(mp.listing_price_count, 0) + batch.cnt))
                            ELSE COALESCE(mp.median_price_usd, 0)
                        END
                    FROM UNNEST($1::bigint[], $2::integer[], $3::numeric[])
                        AS batch(id, cnt, price)
                    WHERE mp.id = batch.id AND mp.id IN (SELECT id FROM locked)
                    """,
                    up_ids, up_counts, up_prices,
                )

        elapsed = time.monotonic() - t0
        write_metrics.total_median_time += elapsed
        write_metrics.median_refreshes += 1
        self._last_flush = time.monotonic()

        logger.info("Median flush: %d products in %.1fms", len(up_ids), elapsed * 1000)
        return len(up_ids)


# ── Write Buffer ─────────────────────────────────────────────

class WriteBuffer:
    """Async write buffer with COPY, adaptive batching, and deferred medians."""

    def __init__(self):
        self._buffer: list[ListingRow] = []
        self._lock = asyncio.Lock()
        self._flush_event = asyncio.Event()
        self._pending_futures: list[asyncio.Future] = []
        self._adaptive_size = FLUSH_SIZE_MIN
        self.median_acc = MedianAccumulator()

    async def enqueue(self, row: ListingRow) -> None:
        """Add a row to the buffer. Caller awaits until flush completes."""
        fut = asyncio.get_running_loop().create_future()
        async with self._lock:
            self._buffer.append(row)
            self._pending_futures.append(fut)
            if len(self._buffer) >= self._adaptive_size:
                self._flush_event.set()
        await fut

    async def flush_loop(self, stop_event: asyncio.Event) -> None:
        """Main flush loop. Runs as a background task."""
        flush_interval = FLUSH_INTERVAL_MS / 1000.0

        while not stop_event.is_set() or self._buffer:
            try:
                await asyncio.wait_for(self._flush_event.wait(), timeout=flush_interval)
            except asyncio.TimeoutError:
                pass

            self._flush_event.clear()

            async with self._lock:
                if not self._buffer:
                    continue
                batch = self._buffer
                futures = self._pending_futures
                self._buffer = []
                self._pending_futures = []

            try:
                await self._flush_batch(batch)
                for fut in futures:
                    if not fut.done():
                        fut.set_result(None)
            except Exception as e:
                logger.error("Flush error (%d rows)", len(batch), exc_info=True)
                write_metrics.errors += len(batch)
                for fut in futures:
                    if not fut.done():
                        fut.set_exception(e)

    async def _flush_batch(self, batch: list[ListingRow]) -> None:
        """Flush: parallel UPSERT listings + COPY price_history.

        Medians are deferred to MedianAccumulator.
        """
        t0 = time.monotonic()
        n = len(batch)

        # Run UPSERT and COPY in parallel
        upsert_task = self._upsert_listings(batch)
        copy_task = self._copy_price_history(batch)

        await asyncio.gather(upsert_task, copy_task)

        # Accumulate medians (deferred, no DB hit)
        await self.median_acc.add_batch(batch)

        elapsed = time.monotonic() - t0
        write_metrics.flushes += 1
        write_metrics.rows_written += n
        write_metrics.total_flush_time += elapsed
        write_metrics.current_batch_size = self._adaptive_size

        # ── Adaptive batch sizing ────────────────────────
        latency_ms = elapsed * 1000
        if latency_ms < _TARGET_LATENCY_MS and self._adaptive_size < FLUSH_SIZE_MAX:
            # Latency is low — increase batch size for more throughput
            self._adaptive_size = min(self._adaptive_size + 64, FLUSH_SIZE_MAX)
        elif latency_ms > _MAX_LATENCY_MS and self._adaptive_size > FLUSH_SIZE_MIN:
            # Latency is too high — reduce batch size
            self._adaptive_size = max(self._adaptive_size - 128, FLUSH_SIZE_MIN)

        if elapsed > 0.1:
            logger.warning("Slow flush: %d rows in %.1fms (adaptive_size→%d)",
                           n, latency_ms, self._adaptive_size)

    async def _upsert_listings(self, batch: list[ListingRow]) -> None:
        """Batch UPSERT product_listings via UNNEST."""
        t1 = time.monotonic()

        # Deduplicate by URL within batch (keep last occurrence)
        seen_urls: dict[str, int] = {}
        for i, r in enumerate(batch):
            seen_urls[r.url] = i
        if len(seen_urls) < len(batch):
            batch = [batch[i] for i in sorted(seen_urls.values())]

        master_product_ids = [r.master_product_id for r in batch]
        titles = [r.title for r in batch]
        normalized_titles = [r.normalized_title for r in batch]
        prices = [r.price for r in batch]
        currencies = [r.currency for r in batch]
        urls = [r.url for r in batch]
        marketplace_ids = [r.marketplace_id for r in batch]
        image_urls = [r.image_url for r in batch]
        similarities = [r.similarity for r in batch]
        scraped_ats = [
            (
                datetime.fromisoformat(r.scraped_at) if isinstance(r.scraped_at, str)
                else r.scraped_at
            )
            for r in batch
        ]
        conditions = [r.condition for r in batch]
        seller_names = [r.seller_name for r in batch]
        seller_ratings = [r.seller_rating for r in batch]
        reviews_counts = [r.reviews_count for r in batch]
        sales_counts = [r.sales_count for r in batch]
        stock_availables = [r.stock_available for r in batch]
        is_free_shippings = [r.is_free_shipping for r in batch]
        shipping_prices = [r.shipping_price for r in batch]

        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO product_listings
                    (master_product_id, title, normalized_title, price, currency,
                     url, marketplace_id, image_url, similarity_score, scraped_at,
                     condition, seller_name, seller_rating, reviews_count, sales_count,
                     stock_available, is_free_shipping, shipping_price)
                SELECT * FROM UNNEST(
                    $1::bigint[], $2::text[], $3::text[], $4::numeric[], $5::text[],
                    $6::text[], $7::text[], $8::text[], $9::real[], $10::timestamptz[],
                    $11::text[], $12::text[], $13::real[], $14::integer[], $15::integer[],
                    $16::integer[], $17::boolean[], $18::numeric[]
                )
                ON CONFLICT (url) DO UPDATE SET
                    price          = EXCLUDED.price,
                    seller_rating  = EXCLUDED.seller_rating,
                    scraped_at     = EXCLUDED.scraped_at,
                    reviews_count  = EXCLUDED.reviews_count,
                    sales_count    = EXCLUDED.sales_count,
                    stock_available = EXCLUDED.stock_available,
                    is_free_shipping = EXCLUDED.is_free_shipping,
                    shipping_price = EXCLUDED.shipping_price
                """,
                master_product_ids, titles, normalized_titles, prices, currencies,
                urls, marketplace_ids, image_urls, similarities, scraped_ats,
                conditions, seller_names, seller_ratings, reviews_counts, sales_counts,
                stock_availables, is_free_shippings, shipping_prices,
            )

        write_metrics.total_upsert_time += time.monotonic() - t1

    async def _copy_price_history(self, batch: list[ListingRow]) -> None:
        """Bulk insert price_history via COPY protocol (5-10x faster than INSERT)."""
        t2 = time.monotonic()

        now = datetime.now(timezone.utc)
        records = [
            (r.url, r.marketplace_id, r.price, r.currency, now)
            for r in batch
        ]

        copy_pool = await get_copy_pool()
        async with copy_pool.acquire() as conn:
            await conn.copy_records_to_table(
                "price_history",
                records=records,
                columns=["listing_url", "marketplace_id", "price", "currency", "recorded_at"],
            )

        write_metrics.total_copy_time += time.monotonic() - t2

    async def flush_remaining(self) -> None:
        """Flush any remaining rows + medians on shutdown."""
        async with self._lock:
            if self._buffer:
                batch = self._buffer
                futures = self._pending_futures
                self._buffer = []
                self._pending_futures = []
            else:
                batch = None
                futures = []

        if batch:
            try:
                await self._flush_batch(batch)
                for fut in futures:
                    if not fut.done():
                        fut.set_result(None)
            except Exception as e:
                for fut in futures:
                    if not fut.done():
                        fut.set_exception(e)

        # Final median flush
        await self.median_acc.flush()


# ── Global singleton ─────────────────────────────────────────
_write_buffer: WriteBuffer | None = None


def get_write_buffer() -> WriteBuffer:
    global _write_buffer
    if _write_buffer is None:
        _write_buffer = WriteBuffer()
    return _write_buffer
