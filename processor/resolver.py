import hashlib
import logging
import os
import statistics
from dataclasses import dataclass

import numpy as np
from pgvector.asyncpg import register_vector  # noqa: F401

from api.currency import FALLBACK_RATES, get_rates, to_usd, fx_convert

from .db import get_pool
from .embeddings import generate_embedding
from .normalizer import extract_brand, extract_model, normalize_title, get_product_type
from .categorizer import classify_product

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.82"))

# Lower threshold when brand+model match confirms identity
BRAND_MODEL_SIMILARITY_THRESHOLD = float(os.getenv("BRAND_MODEL_THRESHOLD", "0.60"))

# Lower threshold when only brand matches (same brand, similar product)
# 0.75 allows more cross-marketplace matches while still preventing
# clearly different products (e.g. WH-1000XM4 vs WF-1000XM4)
BRAND_ONLY_SIMILARITY_THRESHOLD = float(os.getenv("BRAND_ONLY_THRESHOLD", "0.75"))

# Maximum price ratio between a new listing and existing master product listings.
# Prevents matching e.g. "Airpods Pro ($250)" with "Airpods case ($15)".
MATCH_MAX_PRICE_RATIO = float(os.getenv("MATCH_MAX_PRICE_RATIO", "3.5"))


@dataclass
class MatchResult:
    master_product_id: int
    canonical_name: str
    similarity: float
    is_new: bool


async def _match_by_brand_model(conn, brand: str, model: str, embedding) -> dict | None:
    """Try exact brand+model match, verify with embedding similarity."""
    if not brand or not model:
        return None

    row = await conn.fetchrow(
        """
        SELECT id, canonical_name,
               1 - (embedding <=> $1::vector) AS similarity
        FROM master_products
        WHERE brand = $2 AND model = $3
        ORDER BY embedding <=> $1::vector
        LIMIT 1
        """,
        np.array(embedding), brand, model,
    )

    if row and row["similarity"] >= BRAND_MODEL_SIMILARITY_THRESHOLD:
        return dict(row)
    return None


async def _match_by_brand_embedding(conn, brand: str, model: str | None, embedding) -> dict | None:
    """Match within same brand using a relaxed embedding threshold.

    Excludes candidates that have a different model to avoid false merges
    (e.g. Garmin Edge 530 != Garmin Edge 840).
    """
    if not brand:
        return None

    if model:
        # If we have a model, exclude products with a different model
        row = await conn.fetchrow(
            """
            SELECT id, canonical_name,
                   1 - (embedding <=> $1::vector) AS similarity
            FROM master_products
            WHERE brand = $2 AND (model IS NULL OR model = $3)
            ORDER BY embedding <=> $1::vector
            LIMIT 1
            """,
            np.array(embedding), brand, model,
        )
    else:
        row = await conn.fetchrow(
            """
            SELECT id, canonical_name,
                   1 - (embedding <=> $1::vector) AS similarity
            FROM master_products
            WHERE brand = $2
            ORDER BY embedding <=> $1::vector
            LIMIT 1
            """,
            np.array(embedding), brand,
        )

    if row and row["similarity"] >= BRAND_ONLY_SIMILARITY_THRESHOLD:
        return dict(row)
    return None


async def _match_by_embedding(conn, embedding) -> dict | None:
    """Global embedding match with strict threshold."""
    row = await conn.fetchrow(
        """
        SELECT id, canonical_name,
               1 - (embedding <=> $1::vector) AS similarity
        FROM master_products
        ORDER BY embedding <=> $1::vector
        LIMIT 1
        """,
        np.array(embedding),
    )

    if row and row["similarity"] >= SIMILARITY_THRESHOLD:
        return dict(row)
    return None


async def _get_fx_rates() -> dict[str, float]:
    """Fetch live FX rates, falling back to hardcoded rates on failure."""
    try:
        return await get_rates()
    except Exception:
        logger.warning("FX fetch failed in resolver, using fallback rates")
        return FALLBACK_RATES


async def _price_compatible(conn, master_product_id: int, new_price: float, new_currency: str) -> bool:
    """Reject match if price ratio vs existing listings median is too high.

    Prevents grouping e.g. 'Airpods Pro ($250)' with 'Airpods case ($15)'.
    Converts all prices to USD using live FX rates for cross-currency comparison.
    """
    if new_price <= 0:
        return True

    rows = await conn.fetch(
        "SELECT price::float, currency FROM product_listings WHERE master_product_id = $1",
        master_product_id,
    )
    if not rows:
        return True

    rates = await _get_fx_rates()

    existing_usd = [to_usd(r["price"], r["currency"], rates) for r in rows]
    new_usd = to_usd(new_price, new_currency, rates)

    median_usd = statistics.median(existing_usd)
    if median_usd <= 0:
        return True

    ratio = max(new_usd, median_usd) / min(new_usd, median_usd)
    if ratio > MATCH_MAX_PRICE_RATIO:
        logger.info(
            "REJECTED match (price ratio %.1fx): new $%.2f %s (~$%.2f USD) vs median ~$%.2f USD for master_product %d",
            ratio, new_price, new_currency, new_usd, median_usd, master_product_id,
        )
        return False
    return True


def _product_type_compatible(new_title: str, existing_canonical: str) -> bool:
    """Prevent mixing accessories with main products."""
    new_type = get_product_type(new_title)
    existing_type = get_product_type(existing_canonical)
    if new_type != existing_type:
        logger.debug(
            "Product type mismatch: '%s' (%s) vs '%s' (%s)",
            new_title[:50], new_type, existing_canonical[:50], existing_type,
        )
        return False
    return True


async def resolve_product(raw_title: str, price: float = 0.0, currency: str = "USD") -> MatchResult:
    """
    Multi-level product resolution:
    1. Exact brand+model match (threshold 0.65) — catches cross-marketplace same product
    2. Same brand + embedding (threshold 0.80) — catches variants with different descriptions
    3. Global embedding (threshold 0.88) — original strict matching
    4. No match -> create new master product

    Guards:
    - Accessory vs main product type must match.
    - Price ratio check: rejects matches where new listing price differs >3x
      from median price of existing listings (prevents Airpods Pro ↔ Airpods case).
    """
    normalized = normalize_title(raw_title)
    brand = extract_brand(normalized)
    model = extract_model(normalized, brand)
    embedding = generate_embedding(normalized)

    pool = await get_pool()

    async with pool.acquire() as conn:
        # Use a transaction with advisory lock to prevent race conditions
        # creating duplicate master_products for the same normalized title
        async with conn.transaction():
            # Use SHA-256 for a deterministic hash across processes.
            # Python's hash() uses a random seed per process (PYTHONHASHSEED),
            # so two consumers would get different locks for the same title.
            title_hash = int(hashlib.sha256(normalized.encode()).hexdigest()[:15], 16) % (2**31 - 1)
            await conn.execute("SELECT pg_advisory_xact_lock($1)", title_hash)

            # Level 1: Exact brand + model match
            match = await _match_by_brand_model(conn, brand, model, embedding)
            if (match
                    and _product_type_compatible(normalized, match["canonical_name"])
                    and await _price_compatible(conn, match["id"], price, currency)):
                logger.info(
                    "MATCH [brand+model]: '%s' -> '%s' (sim=%.3f)",
                    normalized, match["canonical_name"], match["similarity"],
                )
                return MatchResult(
                    master_product_id=match["id"],
                    canonical_name=match["canonical_name"],
                    similarity=match["similarity"],
                    is_new=False,
                )

            # Level 2: Same brand, relaxed embedding threshold
            match = await _match_by_brand_embedding(conn, brand, model, embedding)
            if (match
                    and _product_type_compatible(normalized, match["canonical_name"])
                    and await _price_compatible(conn, match["id"], price, currency)):
                logger.info(
                    "MATCH [brand+emb]: '%s' -> '%s' (sim=%.3f)",
                    normalized, match["canonical_name"], match["similarity"],
                )
                return MatchResult(
                    master_product_id=match["id"],
                    canonical_name=match["canonical_name"],
                    similarity=match["similarity"],
                    is_new=False,
                )

            # Level 3: Global embedding match (strict)
            match = await _match_by_embedding(conn, embedding)
            if (match
                    and _product_type_compatible(normalized, match["canonical_name"])
                    and await _price_compatible(conn, match["id"], price, currency)):
                logger.info(
                    "MATCH [embedding]: '%s' -> '%s' (sim=%.3f)",
                    normalized, match["canonical_name"], match["similarity"],
                )
                return MatchResult(
                    master_product_id=match["id"],
                    canonical_name=match["canonical_name"],
                    similarity=match["similarity"],
                    is_new=False,
                )

            # No match -> create new master product
            category = classify_product(normalized)
            new_id = await conn.fetchval(
                """
                INSERT INTO master_products (canonical_name, brand, model, category, embedding)
                VALUES ($1, $2, $3, $4, $5::vector)
                RETURNING id
                """,
                normalized, brand, model, category, np.array(embedding),
            )

            logger.info("NEW PRODUCT: '%s' (id=%d, brand=%s, model=%s, cat=%s)", normalized, new_id, brand, model, category)
            return MatchResult(
                master_product_id=new_id,
                canonical_name=normalized,
                similarity=1.0,
                is_new=True,
            )


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
    """Guarda el listing vinculado al master product y registra precio en historial."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # UPSERT: insert new listing or update existing by URL
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

        # Record price history
        await conn.execute(
            """
            INSERT INTO price_history (listing_url, marketplace_id, price, currency)
            VALUES ($1, $2, $3, $4)
            """,
            url, marketplace_id, price, currency,
        )

        return listing_id
