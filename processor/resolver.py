import logging
from dataclasses import dataclass

import numpy as np
from pgvector.asyncpg import register_vector  # noqa: F401

from .db import get_pool
from .embeddings import generate_embedding
from .normalizer import extract_brand, extract_model, normalize_title
from .categorizer import classify_product

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = float(__import__("os").getenv("SIMILARITY_THRESHOLD", "0.85"))


@dataclass
class MatchResult:
    master_product_id: int
    canonical_name: str
    similarity: float
    is_new: bool


async def resolve_product(raw_title: str) -> MatchResult:
    """
    Pipeline completo:
    1. Normaliza el título
    2. Genera embedding
    3. Busca match en DB por cosine similarity
    4. Si similarity > threshold -> vincula; sino -> crea nuevo master product
    """
    normalized = normalize_title(raw_title)
    brand = extract_brand(normalized)
    model = extract_model(normalized, brand)
    embedding = generate_embedding(normalized)

    pool = await get_pool()

    async with pool.acquire() as conn:
        # Buscar el producto más similar
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
            logger.info(
                "MATCH: '%s' -> '%s' (sim=%.3f)",
                normalized, row["canonical_name"], row["similarity"],
            )
            return MatchResult(
                master_product_id=row["id"],
                canonical_name=row["canonical_name"],
                similarity=row["similarity"],
                is_new=False,
            )

        # No match -> crear nuevo master product
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
