import logging
from dataclasses import dataclass

import numpy as np
from pgvector.asyncpg import register_vector  # noqa: F401

from .db import get_pool
from .embeddings import generate_embedding
from .normalizer import extract_brand, extract_model, normalize_title, get_product_type
from .categorizer import classify_product

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = float(__import__("os").getenv("SIMILARITY_THRESHOLD", "0.88"))

# Lower threshold when brand+model match confirms identity
BRAND_MODEL_SIMILARITY_THRESHOLD = 0.65

# Lower threshold when only brand matches (same brand, similar product)
# 0.80 prevents merging different products from same brand
# (e.g. WH-1000XM4 vs WF-1000XM4, Magic Keyboard iMac vs iPad)
BRAND_ONLY_SIMILARITY_THRESHOLD = 0.80


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


async def resolve_product(raw_title: str) -> MatchResult:
    """
    Multi-level product resolution:
    1. Exact brand+model match (threshold 0.55) — catches cross-marketplace same product
    2. Same brand + embedding (threshold 0.70) — catches variants with different descriptions
    3. Global embedding (threshold 0.85) — original strict matching
    4. No match -> create new master product

    Additional guard: accessory vs main product type must match to prevent
    grouping e.g. "Funda Sony WH-1000XM5" with "Audífonos Sony WH-1000XM5".
    """
    normalized = normalize_title(raw_title)
    brand = extract_brand(normalized)
    model = extract_model(normalized, brand)
    embedding = generate_embedding(normalized)

    pool = await get_pool()

    async with pool.acquire() as conn:
        # Level 1: Exact brand + model match
        match = await _match_by_brand_model(conn, brand, model, embedding)
        if match and _product_type_compatible(normalized, match["canonical_name"]):
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
        if match and _product_type_compatible(normalized, match["canonical_name"]):
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
        if match and _product_type_compatible(normalized, match["canonical_name"]):
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
        listing_id = await conn.fetchval(
            """
            INSERT INTO product_listings
                (master_product_id, title, normalized_title, price, currency,
                 url, marketplace_id, image_url, similarity_score, scraped_at,
                 condition, seller_name, seller_rating, reviews_count, sales_count,
                 stock_available, is_free_shipping, shipping_price)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::timestamptz,
                    $11, $12, $13, $14, $15, $16, $17, $18)
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
