"""
Product resolver — matches incoming listings to master products.

Architecture:
  - Matching (read path): In-memory numpy index. ZERO database queries.
    batch_resolve() computes cosine similarity for all listings in one matmul.
  - Creation (write path): INSERT with ON CONFLICT. Only hits DB when no match found.
  - Saving (write path): UPSERT listing + price history in one transaction.

The in-memory index is loaded at startup and refreshed every 2 minutes.
New products are appended immediately after creation.
"""
import hashlib
import logging
import os
import statistics
import time
from dataclasses import dataclass

import numpy as np
from pgvector.asyncpg import register_vector  # noqa: F401

from api.currency import FALLBACK_RATES, get_rates, to_usd, fx_convert

from .db import get_pool
from .embeddings import generate_embedding, generate_embeddings_batch
from .normalizer import extract_brand, extract_model, normalize_title, get_product_type
from .categorizer import classify_product
from .product_index import get_index, ProductIndex

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.82"))
BRAND_MODEL_SIMILARITY_THRESHOLD = float(os.getenv("BRAND_MODEL_THRESHOLD", "0.60"))
BRAND_ONLY_SIMILARITY_THRESHOLD = float(os.getenv("BRAND_ONLY_THRESHOLD", "0.75"))
MATCH_MAX_PRICE_RATIO = float(os.getenv("MATCH_MAX_PRICE_RATIO", "3.5"))

# ── In-memory match cache (LRU, O(1) eviction) ──────────
_MATCH_CACHE_SIZE = int(os.getenv("MATCH_CACHE_SIZE", "16384"))
_MATCH_CACHE_TTL = 600  # 10 minutes

class _LRUMatchCache:
    """O(1) LRU cache with TTL, replacing O(n log n) sorted eviction."""
    __slots__ = ("_data", "_max_size", "_ttl")

    def __init__(self, max_size: int, ttl: float):
        from collections import OrderedDict
        self._data: OrderedDict[str, tuple[float, "MatchResult"]] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl

    def get(self, key: str) -> "MatchResult | None":
        entry = self._data.get(key)
        if entry is None:
            return None
        ts, result = entry
        if time.monotonic() - ts > self._ttl:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return result

    def put(self, key: str, result: "MatchResult") -> None:
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = (time.monotonic(), result)
        while len(self._data) > self._max_size:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)

_match_cache = _LRUMatchCache(_MATCH_CACHE_SIZE, _MATCH_CACHE_TTL)


@dataclass
class MatchResult:
    master_product_id: int
    canonical_name: str
    similarity: float
    is_new: bool


# ── Metrics ──────────────────────────────────────────────────
@dataclass
class ResolverMetrics:
    """Detailed metrics for the resolver layer."""
    cache_hits: int = 0
    index_lookups: int = 0
    index_matches: int = 0
    db_creates: int = 0
    db_create_conflicts: int = 0
    total_resolve_time: float = 0.0
    total_save_time: float = 0.0
    total_queries: int = 0
    pool_wait_time: float = 0.0

    def log(self):
        total = self.cache_hits + self.index_lookups
        hit_rate = (self.cache_hits / total * 100) if total > 0 else 0
        avg_resolve = (self.total_resolve_time / total * 1000) if total > 0 else 0
        avg_save = (self.total_save_time / self.total_queries * 1000) if self.total_queries > 0 else 0
        logger.info(
            "RESOLVER | cache_hit_rate=%.1f%% | index_lookups=%d | index_matches=%d | "
            "db_creates=%d | conflicts=%d | avg_resolve=%.2fms | avg_save=%.2fms | "
            "pool_wait=%.1fms",
            hit_rate, self.index_lookups, self.index_matches,
            self.db_creates, self.db_create_conflicts,
            avg_resolve, avg_save, self.pool_wait_time * 1000,
        )


resolver_metrics = ResolverMetrics()


# ── FX rates ─────────────────────────────────────────────────

async def _get_fx_rates() -> dict[str, float]:
    try:
        return await get_rates()
    except Exception:
        logger.warning("FX fetch failed in resolver, using fallback rates")
        return FALLBACK_RATES


# ── Batch resolve (the main entry point for the pipeline) ────

async def batch_resolve(
    titles: list[str],
    embeddings: list[list[float]],
    prices: list[float],
    currencies: list[str],
) -> list[MatchResult]:
    """Resolve a batch of listings using the in-memory product index.

    This is the primary entry point for the pipeline. It:
    1. Checks the match cache for each listing
    2. For cache misses, uses the in-memory index (single numpy matmul)
    3. For unmatched listings, creates new products in DB

    Returns a MatchResult for each listing in the same order.

    Cost: O(1) cache hits + O(M*N) numpy matmul (M queries, N products)
          + O(K) DB inserts for K new products. Typically K << M.
    """
    t0 = time.monotonic()
    index = get_index()
    rates = await _get_fx_rates()

    M = len(titles)
    results: list[MatchResult | None] = [None] * M

    # ── Step 1: Check cache ──────────────────────────────
    uncached_indices: list[int] = []
    for i in range(M):
        normalized = normalize_title(titles[i])
        cache_key = f"{normalized}:{prices[i]:.2f}:{currencies[i]}"
        cached = _match_cache.get(cache_key)
        if cached is not None:
            results[i] = cached
            resolver_metrics.cache_hits += 1
        else:
            uncached_indices.append(i)

    if not uncached_indices:
        resolver_metrics.total_resolve_time += time.monotonic() - t0
        return results  # type: ignore  # all filled from cache

    # ── Step 2: In-memory index search ───────────────────
    # Prepare batch data for uncached queries
    query_embeddings = np.array(
        [embeddings[i] for i in uncached_indices], dtype=np.float32
    )
    query_brands = []
    query_models = []
    query_prices_usd = []
    query_types = []
    query_normalized = []

    for i in uncached_indices:
        normalized = normalize_title(titles[i])
        brand = extract_brand(normalized)
        model = extract_model(normalized, brand)
        query_normalized.append(normalized)
        query_brands.append(brand)
        query_models.append(model)
        query_prices_usd.append(to_usd(prices[i], currencies[i], rates))
        query_types.append(get_product_type(normalized))

    resolver_metrics.index_lookups += len(uncached_indices)

    if index.is_loaded and index.size > 0:
        search_results = index.batch_search(
            query_embeddings,
            query_brands,
            query_models,
            query_prices_usd,
            query_types,
            brand_model_threshold=BRAND_MODEL_SIMILARITY_THRESHOLD,
            brand_only_threshold=BRAND_ONLY_SIMILARITY_THRESHOLD,
            global_threshold=SIMILARITY_THRESHOLD,
            max_price_ratio=MATCH_MAX_PRICE_RATIO,
        )
    else:
        search_results = [None] * len(uncached_indices)

    # ── Step 3: Process results ──────────────────────────
    new_product_tasks: list[tuple[int, int, str, str | None, str | None, list[float], float, str]] = []
    # (batch_position, uncached_position, normalized, brand, model, embedding, price, currency)

    for j, ui in enumerate(uncached_indices):
        match = search_results[j]
        if match is not None:
            resolver_metrics.index_matches += 1
            result = MatchResult(
                master_product_id=match.product.id,
                canonical_name=match.product.canonical_name,
                similarity=match.similarity,
                is_new=False,
            )
            results[ui] = result
            cache_key = f"{query_normalized[j]}:{prices[ui]:.2f}:{currencies[ui]}"
            _match_cache.put(cache_key, result)
        else:
            # No match — need to create a new product in DB
            new_product_tasks.append((
                ui, j, query_normalized[j], query_brands[j], query_models[j],
                embeddings[ui], prices[ui], currencies[ui],
            ))

    # ── Step 4: Create new products (batch DB write) ────
    if new_product_tasks:
        t_db = time.monotonic()
        pool = await get_pool()

        # Prepare batch arrays, deduplicating by canonical_name within batch
        batch_names = []
        batch_brands = []
        batch_models = []
        batch_categories = []
        batch_embeddings = []
        task_map = []  # (ui, j, normalized, brand, model, embedding, price, currency, category)
        seen_names: set[str] = set()
        deferred_dupes = []  # tasks that share a canonical_name with another in this batch

        for ui, j, normalized, brand, model, embedding, price, currency in new_product_tasks:
            category = classify_product(normalized)
            if normalized not in seen_names:
                seen_names.add(normalized)
                batch_names.append(normalized)
                batch_brands.append(brand)
                batch_models.append(model)
                batch_categories.append(category)
                batch_embeddings.append(np.array(embedding, dtype=np.float32).flatten())
                task_map.append((ui, j, normalized, brand, model, embedding, price, currency, category))
            else:
                # Will resolve after the batch insert using row_map
                deferred_dupes.append((ui, j, normalized, brand, model, embedding, price, currency, category))

        try:
            async with pool.acquire() as conn:
                # Batch insert all new products at once
                rows = await conn.fetch(
                    """
                    INSERT INTO master_products (canonical_name, brand, model, category, embedding)
                    SELECT * FROM UNNEST($1::text[], $2::text[], $3::text[], $4::text[], $5::vector[])
                    ON CONFLICT (canonical_name) DO UPDATE SET updated_at = now()
                    RETURNING id, canonical_name, (xmax = 0) AS was_inserted
                    """,
                    batch_names, batch_brands, batch_models, batch_categories,
                    [np.asarray(e, dtype=np.float32).flatten() for e in batch_embeddings],
                )

            # Build lookup by canonical_name
            row_map = {r["canonical_name"]: r for r in rows}

            new_ids = []
            new_names = []
            new_brands = []
            new_models = []
            new_categories = []
            new_embeddings = []

            for ui, j, normalized, brand, model, embedding, price, currency, category in task_map:
                row = row_map.get(normalized)
                if row is None:
                    continue

                new_id = row["id"]
                was_inserted = row["was_inserted"]

                if was_inserted:
                    resolver_metrics.db_creates += 1
                    new_ids.append(new_id)
                    new_names.append(normalized)
                    new_brands.append(brand)
                    new_models.append(model)
                    new_categories.append(category)
                    new_embeddings.append(embedding)
                else:
                    resolver_metrics.db_create_conflicts += 1

                result = MatchResult(
                    master_product_id=new_id,
                    canonical_name=normalized,
                    similarity=1.0,
                    is_new=was_inserted,
                )
                results[ui] = result
                cache_key = f"{normalized}:{price:.2f}:{currency}"
                _match_cache.put(cache_key, result)

            # Resolve deferred duplicates (same canonical_name within batch)
            for ui, j, normalized, brand, model, embedding, price, currency, category in deferred_dupes:
                row = row_map.get(normalized)
                if row is None:
                    continue
                result = MatchResult(
                    master_product_id=row["id"],
                    canonical_name=normalized,
                    similarity=1.0,
                    is_new=False,
                )
                results[ui] = result
                cache_key = f"{normalized}:{price:.2f}:{currency}"
                _match_cache.put(cache_key, result)

            # Batch append to in-memory index
            if new_ids:
                index.append_batch(new_ids, new_names, new_brands, new_models,
                                   new_categories, new_embeddings)
                logger.info("NEW PRODUCTS: %d created in batch", len(new_ids))

        except Exception:
            logger.error("Batch product creation failed, falling back to individual", exc_info=True)
            for ui, j, normalized, brand, model, embedding, price, currency, category in task_map:
                try:
                    async with pool.acquire() as conn:
                        row = await conn.fetchrow(
                            """
                            INSERT INTO master_products (canonical_name, brand, model, category, embedding)
                            VALUES ($1, $2, $3, $4, $5::vector)
                            ON CONFLICT (canonical_name) DO UPDATE SET updated_at = now()
                            RETURNING id, (xmax = 0) AS was_inserted
                            """,
                            normalized, brand, model, category, np.array(embedding).flatten(),
                        )
                    new_id = row["id"]
                    was_inserted = row["was_inserted"]
                    if was_inserted:
                        resolver_metrics.db_creates += 1
                        index.append(new_id, normalized, brand, model, category, embedding)
                    else:
                        resolver_metrics.db_create_conflicts += 1
                    result = MatchResult(
                        master_product_id=new_id, canonical_name=normalized,
                        similarity=1.0, is_new=was_inserted,
                    )
                    results[ui] = result
                    cache_key = f"{normalized}:{price:.2f}:{currency}"
                    _match_cache.put(cache_key, result)
                except Exception:
                    logger.error("Error creating product '%s'", normalized[:50], exc_info=True)

        resolver_metrics.pool_wait_time += time.monotonic() - t_db

    resolver_metrics.total_resolve_time += time.monotonic() - t0

    # Fill any remaining None results (shouldn't happen, but safety)
    for i in range(M):
        if results[i] is None:
            logger.error("Unresolved listing at index %d: '%s'", i, titles[i][:50])
            # Create a dummy result to avoid pipeline crash
            results[i] = MatchResult(
                master_product_id=-1,
                canonical_name="__unresolved__",
                similarity=0.0,
                is_new=False,
            )

    return results  # type: ignore


# ── Legacy single-item resolve (for consumer.py backward compat) ──

async def resolve_product(raw_title: str, price: float = 0.0, currency: str = "USD") -> MatchResult:
    """Single-item resolve (legacy path for consumer.py)."""
    normalized = normalize_title(raw_title)
    embedding = generate_embedding(normalized)
    results = await batch_resolve([raw_title], [embedding], [price], [currency])
    return results[0]


async def resolve_product_with_embedding(
    raw_title: str,
    embedding: list[float],
    price: float = 0.0,
    currency: str = "USD",
) -> MatchResult:
    """Single-item resolve with pre-computed embedding (legacy path)."""
    results = await batch_resolve([raw_title], [embedding], [price], [currency])
    return results[0]


# ── Save listing (single transaction) ────────────────────────

async def save_listing(
    master_product_id: int,
    title: str,
    normalized_title: str,
    price: float,
    currency: str,
    url: str,
    marketplace_id: str,
    image_url: str | None,
    similarity: float,
    scraped_at: str | None,
    condition: str = "new",
    seller_name: str | None = None,
    seller_rating: float | None = None,
    reviews_count: int = 0,
    sales_count: int = 0,
    stock_available: int | None = None,
    is_free_shipping: bool = False,
    shipping_price: float | None = None,
) -> int:
    """Save listing + price history + update median in a single transaction."""
    t0 = time.monotonic()
    rates = await _get_fx_rates()
    new_usd = to_usd(price, currency, rates)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            listing_id = await conn.fetchval(
                """
                INSERT INTO product_listings
                    (master_product_id, title, normalized_title, price, currency,
                     url, marketplace_id, image_url, similarity_score, scraped_at,
                     condition, seller_name, seller_rating, reviews_count, sales_count,
                     stock_available, is_free_shipping, shipping_price)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::timestamptz,
                        $11, $12, $13, $14, $15, $16, $17, $18)
                ON CONFLICT (url) DO UPDATE SET
                    price          = EXCLUDED.price,
                    seller_rating  = EXCLUDED.seller_rating,
                    scraped_at     = EXCLUDED.scraped_at,
                    reviews_count  = EXCLUDED.reviews_count,
                    sales_count    = EXCLUDED.sales_count,
                    stock_available = EXCLUDED.stock_available,
                    is_free_shipping = EXCLUDED.is_free_shipping,
                    shipping_price = EXCLUDED.shipping_price
                RETURNING id
                """,
                master_product_id, title, normalized_title, price, currency,
                url, marketplace_id, image_url, similarity, scraped_at,
                condition, seller_name, seller_rating, reviews_count, sales_count,
                stock_available, is_free_shipping, shipping_price,
            )

            await conn.execute(
                """
                WITH ph AS (
                    INSERT INTO price_history (listing_url, marketplace_id, price, currency)
                    VALUES ($1, $2, $3, $4)
                )
                UPDATE master_products
                SET listing_price_count = COALESCE(listing_price_count, 0) + 1,
                    median_price_usd = CASE
                        WHEN COALESCE(listing_price_count, 0) = 0 THEN $5
                        WHEN $5 > COALESCE(median_price_usd, 0)
                            THEN COALESCE(median_price_usd, 0) + (($5 - COALESCE(median_price_usd, 0)) / (COALESCE(listing_price_count, 0) + 1))
                        WHEN $5 < COALESCE(median_price_usd, 0)
                            THEN COALESCE(median_price_usd, 0) - ((COALESCE(median_price_usd, 0) - $5) / (COALESCE(listing_price_count, 0) + 1))
                        ELSE COALESCE(median_price_usd, 0)
                    END
                WHERE id = $6
                """,
                url, marketplace_id, price, currency,
                new_usd, master_product_id,
            )

        resolver_metrics.total_save_time += time.monotonic() - t0
        resolver_metrics.total_queries += 1
        return listing_id


# ── Batch median recalculation ───────────────────────────────

async def recalculate_medians() -> int:
    """Recalculate true median_price_usd for all master products."""
    pool = await get_pool()
    rates = await _get_fx_rates()
    count = 0

    async with pool.acquire() as conn:
        product_ids = await conn.fetch(
            "SELECT id FROM master_products WHERE listing_price_count > 0"
        )
        for row in product_ids:
            pid = row["id"]
            listings = await conn.fetch(
                "SELECT price::float, currency FROM product_listings WHERE master_product_id = $1",
                pid,
            )
            if not listings:
                continue

            usd_prices = [to_usd(r["price"], r["currency"], rates) for r in listings]
            median = statistics.median(usd_prices)

            await conn.execute(
                "UPDATE master_products SET median_price_usd = $1, listing_price_count = $2 WHERE id = $3",
                median, len(listings), pid,
            )
            count += 1

    logger.info("Recalculated medians for %d products", count)
    return count
