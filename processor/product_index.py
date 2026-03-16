"""
In-memory product index for ultra-fast batch similarity search.

Replaces pgvector queries with numpy matrix operations.
For 50k products at 384 dimensions:
  - Memory: ~73 MB
  - Batch search (64 queries): ~1ms (vs ~640ms for 64 pgvector queries)

Architecture:
  - On startup, loads all master_products into numpy arrays
  - Product metadata stored in parallel lists for O(1) access by index
  - Embeddings stored as a normalized (N, 384) float32 matrix
  - Similarity = batch matrix multiply (cosine, since embeddings are normalized)
  - New products appended on creation; full refresh every REFRESH_INTERVAL
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

REFRESH_INTERVAL = int(os.getenv("INDEX_REFRESH_INTERVAL", "120"))  # seconds


@dataclass
class ProductMeta:
    """Metadata for a single master product (non-embedding fields)."""
    id: int
    canonical_name: str
    brand: str | None
    model: str | None
    category: str | None
    median_price_usd: float
    listing_price_count: int
    product_type: str  # "main" or "accessory"


class ProductIndex:
    """In-memory index of all master products for batch similarity search.

    Thread-safe for reads. Writes (append/refresh) are protected by an asyncio.Lock.
    """

    def __init__(self):
        # Parallel arrays — index i in all arrays corresponds to the same product
        self._embeddings: np.ndarray | None = None  # shape (N, 384), float32, L2-normalized
        self._products: list[ProductMeta] = []
        self._id_to_idx: dict[int, int] = {}  # product_id → index in arrays
        self._lock = asyncio.Lock()
        self._loaded = False
        self._last_refresh = 0.0
        self._product_count = 0

        # Hierarchical indexes for O(1) candidate lookup
        self._brand_model_idx: dict[tuple[str, str], list[int]] = {}  # (brand, model) -> [indices]
        self._brand_idx: dict[str, list[int]] = {}                     # brand -> [indices]

        # Append buffer — avoids per-item vstack
        self._append_buffer: list[tuple[int, str, str | None, str | None, str | None, list[float]]] = []
        self._APPEND_BUFFER_SIZE = 50

    @property
    def size(self) -> int:
        return self._product_count

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    async def load(self, pool) -> None:
        """Load all master products from DB into memory (copy-on-write)."""
        t0 = time.monotonic()

        # Phase 1: Fetch data OUTSIDE the lock (DB I/O, ~300ms)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, canonical_name, brand, model, category,
                       COALESCE(median_price_usd, 0)::float AS median_price_usd,
                       COALESCE(listing_price_count, 0) AS listing_price_count,
                       embedding::float4[]
                FROM master_products
                ORDER BY id
                """
            )

        if not rows:
            async with self._lock:
                self._embeddings = np.empty((0, 384), dtype=np.float32)
                self._products = []
                self._id_to_idx = {}
                self._product_count = 0
                self._brand_model_idx = {}
                self._brand_idx = {}
                self._loaded = True
                self._last_refresh = time.monotonic()
            return

        # Phase 2: Build new structures OUTSIDE the lock (CPU, ~200ms)
        from .normalizer import get_product_type

        products = []
        embeddings = []
        id_to_idx = {}

        for i, row in enumerate(rows):
            meta = ProductMeta(
                id=row["id"],
                canonical_name=row["canonical_name"],
                brand=row["brand"],
                model=row["model"],
                category=row["category"],
                median_price_usd=row["median_price_usd"],
                listing_price_count=row["listing_price_count"],
                product_type=get_product_type(row["canonical_name"]),
            )
            products.append(meta)
            embeddings.append(row["embedding"])
            id_to_idx[row["id"]] = i

        emb_matrix = np.array(embeddings, dtype=np.float32)
        norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        emb_matrix = emb_matrix / norms

        # Build hierarchical indexes
        brand_model_idx: dict[tuple[str, str], list[int]] = {}
        brand_idx: dict[str, list[int]] = {}
        for i, p in enumerate(products):
            if p.brand:
                brand_idx.setdefault(p.brand, []).append(i)
                if p.model:
                    brand_model_idx.setdefault((p.brand, p.model), []).append(i)

        # Phase 3: Atomic swap UNDER lock (<1ms)
        async with self._lock:
            self._embeddings = emb_matrix
            self._products = products
            self._id_to_idx = id_to_idx
            self._product_count = len(products)
            self._brand_model_idx = brand_model_idx
            self._brand_idx = brand_idx
            self._loaded = True
            self._last_refresh = time.monotonic()

        elapsed = time.monotonic() - t0
        mem_mb = emb_matrix.nbytes / 1024 / 1024
        logger.info(
            "Product index loaded: %d products, %.1f MB, %.1fs",
            len(products), mem_mb, elapsed,
        )

    def append(self, product_id: int, canonical_name: str, brand: str | None,
               model: str | None, category: str | None, embedding: list[float]) -> None:
        """Buffer a new product for batch append (avoids per-item vstack)."""
        self._append_buffer.append((product_id, canonical_name, brand, model, category, embedding))
        if len(self._append_buffer) >= self._APPEND_BUFFER_SIZE:
            self._flush_append_buffer()

    def _flush_append_buffer(self) -> None:
        """Flush buffered appends as a single batch vstack."""
        if not self._append_buffer:
            return
        pids, names, brands, models, cats, embs = zip(*self._append_buffer)
        self.append_batch(list(pids), list(names), list(brands), list(models), list(cats), list(embs))
        self._append_buffer.clear()

    def append_batch(self, product_ids: list[int], canonical_names: list[str],
                     brands: list[str | None], models: list[str | None],
                     categories: list[str | None], embeddings_list: list[list[float]]) -> None:
        """Append multiple newly created products to the index in one operation."""
        if not product_ids:
            return

        from .normalizer import get_product_type

        new_metas = []
        new_embs = np.empty((len(product_ids), 384), dtype=np.float32)

        for i, (pid, name, brand, model, cat, emb) in enumerate(
            zip(product_ids, canonical_names, brands, models, categories, embeddings_list)
        ):
            new_metas.append(ProductMeta(
                id=pid, canonical_name=name, brand=brand, model=model,
                category=cat, median_price_usd=0.0, listing_price_count=0,
                product_type=get_product_type(name),
            ))
            e = np.array(emb, dtype=np.float32)
            norm = np.linalg.norm(e)
            if norm > 0:
                e = e / norm
            new_embs[i] = e

        # Single vstack for the whole batch
        if self._embeddings is not None and len(self._embeddings) > 0:
            new_embeddings = np.vstack([self._embeddings, new_embs])
        else:
            new_embeddings = new_embs

        base_idx = len(self._products)
        new_products = self._products + new_metas
        new_id_to_idx = {**self._id_to_idx}
        for i, pid in enumerate(product_ids):
            new_id_to_idx[pid] = base_idx + i

        # Atomic swap
        self._embeddings = new_embeddings
        self._products = new_products
        self._id_to_idx = new_id_to_idx
        self._product_count = len(new_products)
        self._rebuild_hierarchical_index()

    def _rebuild_hierarchical_index(self) -> None:
        """Build brand/model lookup indexes for O(1) candidate filtering."""
        brand_model_idx: dict[tuple[str, str], list[int]] = {}
        brand_idx: dict[str, list[int]] = {}
        for i, p in enumerate(self._products):
            if p.brand:
                brand_idx.setdefault(p.brand, []).append(i)
                if p.model:
                    brand_model_idx.setdefault((p.brand, p.model), []).append(i)
        self._brand_model_idx = brand_model_idx
        self._brand_idx = brand_idx

    def needs_refresh(self) -> bool:
        return (time.monotonic() - self._last_refresh) > REFRESH_INTERVAL

    # ── Batch search ─────────────────────────────────────────

    @dataclass
    class SearchResult:
        """Result of a single similarity search."""
        product_idx: int  # index in the internal arrays
        product: "ProductMeta"
        similarity: float

    def batch_search(
        self,
        query_embeddings: np.ndarray,
        brands: list[str | None],
        models: list[str | None],
        prices_usd: list[float],
        product_types: list[str],
        brand_model_threshold: float = 0.60,
        brand_only_threshold: float = 0.75,
        global_threshold: float = 0.82,
        max_price_ratio: float = 3.5,
    ) -> list["ProductIndex.SearchResult | None"]:
        """Search for best matching products for a batch of queries.

        Args:
            query_embeddings: (M, 384) normalized float32 matrix
            brands: brand for each query (or None)
            models: model for each query (or None)
            prices_usd: USD price for each query (for price compatibility check)
            product_types: "main" or "accessory" for each query

        Returns:
            List of SearchResult (one per query), or None if no match found.

        Strategy (progressive narrowing per query):
            1. Compute similarities for ALL products in one matmul
            2. For each query, apply filters in order:
               a) brand+model match → threshold 0.60
               b) brand-only match → threshold 0.75
               c) global match → threshold 0.82
            3. Apply product_type and price compatibility guards
        """
        # Flush any buffered appends before searching
        if self._append_buffer:
            self._flush_append_buffer()

        if self._product_count == 0 or self._embeddings is None:
            return [None] * len(brands)

        M = query_embeddings.shape[0]
        N = self._product_count

        # Normalize query embeddings
        norms = np.linalg.norm(query_embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        q_norm = query_embeddings / norms

        # Single matrix multiply: (M, 384) @ (384, N) → (M, N) similarities
        similarities = q_norm @ self._embeddings.T  # cosine similarity

        # Pre-compute product attributes for vectorized filtering
        p_brands = [p.brand for p in self._products]
        p_models = [p.model for p in self._products]
        p_types = [p.product_type for p in self._products]
        p_medians = [p.median_price_usd for p in self._products]
        p_counts = [p.listing_price_count for p in self._products]

        results: list[ProductIndex.SearchResult | None] = []

        for i in range(M):
            sims = similarities[i]  # (N,) similarities for query i
            q_brand = brands[i]
            q_model = models[i]
            q_price = prices_usd[i]
            q_type = product_types[i]

            match = self._find_best_match(
                sims, q_brand, q_model, q_price, q_type,
                p_brands, p_models, p_types, p_medians, p_counts,
                brand_model_threshold, brand_only_threshold,
                global_threshold, max_price_ratio,
            )
            results.append(match)

        return results

    def _find_best_match(
        self,
        sims: np.ndarray,
        q_brand: str | None,
        q_model: str | None,
        q_price: float,
        q_type: str,
        p_brands: list,
        p_models: list,
        p_types: list,
        p_medians: list,
        p_counts: list,
        brand_model_threshold: float,
        brand_only_threshold: float,
        global_threshold: float,
        max_price_ratio: float,
    ) -> "ProductIndex.SearchResult | None":
        """Find best match using hierarchical index for O(K) instead of O(N)."""

        # Level 1: brand + model exact match — O(K) where K << N
        if q_brand and q_model:
            candidates = self._brand_model_idx.get((q_brand, q_model), [])
            best_sim = -1.0
            best_idx = -1
            for j in candidates:
                if (sims[j] > best_sim
                        and sims[j] >= brand_model_threshold
                        and p_types[j] == q_type
                        and self._price_ok(q_price, p_medians[j], p_counts[j], max_price_ratio)):
                    best_sim = sims[j]
                    best_idx = j
            if best_idx >= 0:
                return ProductIndex.SearchResult(
                    product_idx=best_idx,
                    product=self._products[best_idx],
                    similarity=float(best_sim),
                )

        # Level 2: brand-only match — O(K) where K = products of same brand
        if q_brand:
            candidates = self._brand_idx.get(q_brand, [])
            best_sim = -1.0
            best_idx = -1
            for j in candidates:
                if ((p_models[j] is None or p_models[j] == q_model or q_model is None)
                        and sims[j] > best_sim
                        and sims[j] >= brand_only_threshold
                        and p_types[j] == q_type
                        and self._price_ok(q_price, p_medians[j], p_counts[j], max_price_ratio)):
                    best_sim = sims[j]
                    best_idx = j
            if best_idx >= 0:
                return ProductIndex.SearchResult(
                    product_idx=best_idx,
                    product=self._products[best_idx],
                    similarity=float(best_sim),
                )

        # Level 3: global embedding match (strict) — O(1) via argmax
        best_idx_global = int(np.argmax(sims))
        best_sim_global = float(sims[best_idx_global])
        if (best_sim_global >= global_threshold
                and p_types[best_idx_global] == q_type
                and self._price_ok(q_price, p_medians[best_idx_global],
                                   p_counts[best_idx_global], max_price_ratio)):
            return ProductIndex.SearchResult(
                product_idx=best_idx_global,
                product=self._products[best_idx_global],
                similarity=best_sim_global,
            )

        return None

    @staticmethod
    def _price_ok(new_usd: float, median_usd: float, count: int, max_ratio: float) -> bool:
        """Check price compatibility using materialized median."""
        if new_usd <= 0 or median_usd <= 0 or count < 3:
            return True
        ratio = max(new_usd, median_usd) / min(new_usd, median_usd)
        return ratio <= max_ratio

    def get_product(self, product_id: int) -> ProductMeta | None:
        """Look up a product by ID."""
        idx = self._id_to_idx.get(product_id)
        if idx is None:
            return None
        return self._products[idx]


# ── Global singleton ─────────────────────────────────────────
_index: ProductIndex | None = None


def get_index() -> ProductIndex:
    global _index
    if _index is None:
        _index = ProductIndex()
    return _index
