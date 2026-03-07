import logging
from dataclasses import dataclass

import numpy as np
from pgvector.asyncpg import register_vector  # noqa: F401

from .db import get_pool
from .embeddings import generate_embedding
from .normalizer import extract_brand, extract_model, normalize_title

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
        new_id = await conn.fetchval(
            """
            INSERT INTO master_products (canonical_name, brand, model, embedding)
            VALUES ($1, $2, $3, $4::vector)
            RETURNING id
            """,
            normalized, brand, model, np.array(embedding),
        )

        logger.info("NEW PRODUCT: '%s' (id=%d, brand=%s, model=%s)", normalized, new_id, brand, model)
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
) -> int:
    """Guarda el listing vinculado al master product."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO product_listings
                (master_product_id, title, normalized_title, price, currency,
                 url, marketplace_id, image_url, similarity_score, scraped_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::timestamptz)
            RETURNING id
            """,
            master_product_id, title, normalized_title, price, currency,
            url, marketplace_id, image_url, similarity, scraped_at,
        )
