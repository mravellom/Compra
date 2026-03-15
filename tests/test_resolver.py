"""
Tests — Product Resolver (In-Memory Index)

Verifica matching vía batch_resolve() con índice numpy en memoria:
  - Alta similitud   → vincula al producto existente (is_new=False)
  - Baja similitud   → crea producto nuevo           (is_new=True)
  - Index vacío      → crea primer producto           (is_new=True)
  - Borderline above → vincula (global threshold 0.82)
  - Borderline below → crea nuevo
"""
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from processor.normalizer import normalize_title, extract_brand, extract_model
from processor.product_index import ProductIndex
from processor.resolver import MatchResult, batch_resolve, _match_cache


# ─── Helpers ────────────────────────────────────────────────────────

def make_fake_embedding(seed: int, dim: int = 384) -> list[float]:
    """Genera un embedding normalizado y determinista."""
    rng = np.random.RandomState(seed)
    vec = rng.randn(dim).astype(np.float32)
    return (vec / np.linalg.norm(vec)).tolist()


def make_similar_embedding(base: list[float], target_sim: float, dim: int = 384) -> list[float]:
    """Create a normalized embedding with a specific cosine similarity to base.

    Uses Gram-Schmidt orthogonalization to construct a vector at exactly
    arccos(target_sim) radians from base.
    """
    b = np.array(base, dtype=np.float32)
    b = b / np.linalg.norm(b)

    rng = np.random.RandomState(999)
    rand_vec = rng.randn(dim).astype(np.float32)
    orth = rand_vec - np.dot(rand_vec, b) * b
    orth = orth / np.linalg.norm(orth)

    theta = np.arccos(np.clip(target_sim, -1.0, 1.0))
    result = float(np.cos(theta)) * b + float(np.sin(theta)) * orth
    result = result / np.linalg.norm(result)
    return result.tolist()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Calcula similitud coseno entre dos vectores."""
    a_arr, b_arr = np.array(a), np.array(b)
    return float(np.dot(a_arr, b_arr) / (np.linalg.norm(a_arr) * np.linalg.norm(b_arr)))


def build_index(products: list[tuple]) -> ProductIndex:
    """Create a populated ProductIndex.

    Each product: (id, canonical_name, brand, model, embedding)
    """
    index = ProductIndex()
    for pid, name, brand, model, emb in products:
        index.append(pid, name, brand, model, None, emb)
    index._loaded = True
    return index


class _FakeAcquire:
    """Simula pool.acquire() como async context manager."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass


FAKE_RATES = {
    "USD": 1.0, "ARS": 1450.0, "MXN": 17.8,
    "EUR": 0.86, "GBP": 0.75, "BRL": 5.10,
    "CLP": 950.0, "COP": 4200.0, "CNY": 7.2,
}


def _patch_resolver(mocker, index, mock_db_rows=None):
    """Patch get_index, get_pool, and FX rates for batch_resolve tests.

    Args:
        index: ProductIndex to return from get_index().
        mock_db_rows: If given, list[dict] for the DB INSERT RETURNING response
                      (needed when batch_resolve creates new products).
    Returns:
        mock_conn (AsyncMock) if mock_db_rows was provided, else None.
    """
    mocker.patch("processor.resolver.get_index", return_value=index)

    async def fake_rates():
        return FAKE_RATES
    mocker.patch("processor.resolver.get_rates", side_effect=fake_rates)

    if mock_db_rows is not None:
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=mock_db_rows)

        pool = MagicMock()
        pool.acquire.return_value = _FakeAcquire(mock_conn)

        async def fake_get_pool():
            return pool
        mocker.patch("processor.resolver.get_pool", side_effect=fake_get_pool)

        return mock_conn

    return None


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear resolver match cache before/after each test."""
    _match_cache.clear()
    yield
    _match_cache.clear()


# ─── Tests de matching ──────────────────────────────────────────────

class TestResolverMatching:

    @pytest.mark.asyncio
    async def test_high_similarity_links_to_existing(self, mocker):
        """Embedding idéntico → vincula al producto existente (is_new=False)."""
        emb = make_fake_embedding(42)
        index = build_index([(1, "sony wh1000xm4", "sony", "wh1000xm4", emb)])
        _patch_resolver(mocker, index)

        results = await batch_resolve(
            titles=["Sony WH-1000XM4 Headphones"],
            embeddings=[emb],
            prices=[299.0],
            currencies=["USD"],
        )

        assert results[0].is_new is False
        assert results[0].master_product_id == 1
        assert results[0].canonical_name == "sony wh1000xm4"
        assert results[0].similarity > 0.95

    @pytest.mark.asyncio
    async def test_low_similarity_creates_new_product(self, mocker):
        """Baja similitud → crea un nuevo master product (is_new=True)."""
        existing_emb = make_fake_embedding(42)
        query_emb = make_fake_embedding(99)  # vector muy diferente

        index = build_index([
            (5, "bose quietcomfort 45", "bose", "quietcomfort45", existing_emb),
        ])

        title = "Completely Different Product XYZ"
        expected_name = normalize_title(title)

        _patch_resolver(mocker, index, mock_db_rows=[
            {"id": 10, "canonical_name": expected_name, "was_inserted": True},
        ])

        results = await batch_resolve(
            titles=[title],
            embeddings=[query_emb],
            prices=[50.0],
            currencies=["USD"],
        )

        assert results[0].is_new is True
        assert results[0].master_product_id == 10
        assert results[0].similarity == 1.0

    @pytest.mark.asyncio
    async def test_empty_db_creates_first_product(self, mocker):
        """Index vacío → crea el primer producto."""
        emb = make_fake_embedding(1)
        index = build_index([])  # empty

        title = "Sony WH-1000XM4"
        expected_name = normalize_title(title)

        _patch_resolver(mocker, index, mock_db_rows=[
            {"id": 1, "canonical_name": expected_name, "was_inserted": True},
        ])

        results = await batch_resolve(
            titles=[title],
            embeddings=[emb],
            prices=[299.0],
            currencies=["USD"],
        )

        assert results[0].is_new is True
        assert results[0].master_product_id == 1

    @pytest.mark.asyncio
    async def test_borderline_088_matches(self, mocker):
        """Similitud 0.84 (above global threshold 0.82) → DEBE matchear.

        Uses different brand to force global-level matching.
        """
        base_emb = make_fake_embedding(42)
        query_emb = make_similar_embedding(base_emb, 0.84)

        # Different brand → brand/model levels won't match → falls to global (0.82)
        index = build_index([
            (3, "generic headphones model z", None, None, base_emb),
        ])
        _patch_resolver(mocker, index)

        results = await batch_resolve(
            titles=["Another Generic Headphones Set"],
            embeddings=[query_emb],
            prices=[100.0],
            currencies=["USD"],
        )

        assert results[0].is_new is False
        assert results[0].master_product_id == 3
        assert results[0].similarity >= 0.82

    @pytest.mark.asyncio
    async def test_borderline_087_creates_new(self, mocker):
        """Similitud 0.80 (below global threshold 0.82) → DEBE crear nuevo."""
        base_emb = make_fake_embedding(42)
        query_emb = make_similar_embedding(base_emb, 0.80)

        index = build_index([
            (3, "generic headphones model z", None, None, base_emb),
        ])

        title = "Something Almost Similar"
        expected_name = normalize_title(title)

        _patch_resolver(mocker, index, mock_db_rows=[
            {"id": 20, "canonical_name": expected_name, "was_inserted": True},
        ])

        results = await batch_resolve(
            titles=[title],
            embeddings=[query_emb],
            prices=[100.0],
            currencies=["USD"],
        )

        assert results[0].is_new is True
        assert results[0].master_product_id == 20

    @pytest.mark.asyncio
    async def test_insert_receives_correct_brand_and_model(self, mocker):
        """Verifica que el INSERT pase brand y model correctos."""
        emb = make_fake_embedding(5)
        index = build_index([])  # empty → forces creation

        title = "Sony WH-1000XM4 Wireless"
        expected_name = normalize_title(title)
        expected_brand = extract_brand(expected_name)
        expected_model = extract_model(expected_name, expected_brand)

        mock_conn = _patch_resolver(mocker, index, mock_db_rows=[
            {"id": 50, "canonical_name": expected_name, "was_inserted": True},
        ])

        results = await batch_resolve(
            titles=[title],
            embeddings=[emb],
            prices=[299.0],
            currencies=["USD"],
        )

        assert results[0].is_new is True
        assert results[0].master_product_id == 50

        # Verify the DB INSERT was called with correct brand/model
        mock_conn.fetch.assert_called_once()
        call_args = mock_conn.fetch.call_args[0]
        sql = call_args[0]
        batch_names = call_args[1]
        batch_brands = call_args[2]
        batch_models = call_args[3]

        assert "INSERT INTO master_products" in sql
        assert expected_name in batch_names
        assert expected_brand in batch_brands
        assert expected_model in batch_models


# ─── Tests de stable hash ──────────────────────────────────────────

class TestStableHash:
    """Verify the advisory lock hash is deterministic across calls."""

    def test_sha256_hash_is_deterministic(self):
        """Same input must always produce the same hash value."""
        import hashlib
        text = "sony wh1000xm4 audifonos bluetooth"
        h1 = int(hashlib.sha256(text.encode()).hexdigest()[:15], 16) % (2**31 - 1)
        h2 = int(hashlib.sha256(text.encode()).hexdigest()[:15], 16) % (2**31 - 1)
        assert h1 == h2

    def test_different_inputs_produce_different_hashes(self):
        import hashlib
        t1 = "sony wh1000xm4"
        t2 = "apple airpods pro"
        h1 = int(hashlib.sha256(t1.encode()).hexdigest()[:15], 16) % (2**31 - 1)
        h2 = int(hashlib.sha256(t2.encode()).hexdigest()[:15], 16) % (2**31 - 1)
        assert h1 != h2

    def test_hash_fits_in_pg_int4(self):
        """Advisory lock requires a 32-bit signed int."""
        import hashlib
        text = "test product title with unicode café"
        h = int(hashlib.sha256(text.encode()).hexdigest()[:15], 16) % (2**31 - 1)
        assert 0 <= h < 2**31


# ─── Tests de cosine similarity (validación matemática) ─────────────

class TestCosineSimilarityMath:

    def test_identical_vectors_sim_1(self):
        v = make_fake_embedding(42)
        assert cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-6)

    def test_orthogonal_vectors_sim_0(self):
        assert cosine_similarity([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0, abs=1e-6)

    def test_opposite_vectors_sim_negative_1(self):
        assert cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0, abs=1e-6)

    def test_small_perturbation_high_similarity(self):
        base = make_fake_embedding(42)
        noise = np.array(base) + np.random.RandomState(0).randn(384) * 0.01
        perturbed = (noise / np.linalg.norm(noise)).tolist()
        assert cosine_similarity(base, perturbed) > 0.95

    def test_make_similar_embedding_produces_target_sim(self):
        """Verify that make_similar_embedding produces the requested similarity."""
        base = make_fake_embedding(42)
        for target in [0.50, 0.75, 0.82, 0.90, 0.99]:
            similar = make_similar_embedding(base, target)
            actual = cosine_similarity(base, similar)
            assert actual == pytest.approx(target, abs=0.01)
