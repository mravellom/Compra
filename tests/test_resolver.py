"""
Integration Tests — Vector DB (Product Resolver)

Usa pytest-mock (mocker) para simular pgvector.
Lógica de negocio verificada:
  - similarity >= 0.85 → vincula al ID existente (is_new=False)
  - similarity  = 0.84 → crea producto nuevo    (is_new=True)
  - DB vacía            → crea primer producto   (is_new=True)
"""
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest


# ─── Helpers ────────────────────────────────────────────────────────

def make_fake_embedding(seed: int, dim: int = 384) -> list[float]:
    """Genera un embedding normalizado y determinista."""
    rng = np.random.RandomState(seed)
    vec = rng.randn(dim).astype(np.float32)
    return (vec / np.linalg.norm(vec)).tolist()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Calcula similitud coseno entre dos vectores."""
    a_arr, b_arr = np.array(a), np.array(b)
    return float(np.dot(a_arr, b_arr) / (np.linalg.norm(a_arr) * np.linalg.norm(b_arr)))


class _FakeAcquire:
    """Simula pool.acquire() — sync call que retorna async context manager."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass


def _patch_resolver(mocker, mock_conn, embedding_seed=42):
    """Aplica los patches comunes a get_pool y generate_embedding."""
    # pool.acquire() es sync (no awaited), retorna async ctx manager
    pool = MagicMock()
    pool.acquire.return_value = _FakeAcquire(mock_conn)

    # get_pool() es async, retorna el pool
    async def fake_get_pool():
        return pool

    mocker.patch("processor.resolver.get_pool", side_effect=fake_get_pool)
    mocker.patch("processor.resolver.generate_embedding", return_value=make_fake_embedding(embedding_seed))


# ─── Tests de matching ──────────────────────────────────────────────

class TestResolverMatching:

    @pytest.mark.asyncio
    async def test_high_similarity_links_to_existing(self, mocker):
        """similarity=0.99 → debe vincular al producto existente (is_new=False)."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": 1, "canonical_name": "sony wh1000xm4", "similarity": 0.99,
        }
        _patch_resolver(mocker, mock_conn, 42)

        from processor.resolver import resolve_product
        result = await resolve_product("Sony WH-1000XM4 Headphones")

        assert result.is_new is False
        assert result.master_product_id == 1
        assert result.canonical_name == "sony wh1000xm4"
        assert result.similarity == 0.99
        mock_conn.fetchval.assert_not_called()

    @pytest.mark.asyncio
    async def test_low_similarity_creates_new_product(self, mocker):
        """similarity=0.42 → debe crear un nuevo master product (is_new=True)."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": 5, "canonical_name": "bose quietcomfort 45", "similarity": 0.42,
        }
        mock_conn.fetchval.return_value = 10
        _patch_resolver(mocker, mock_conn, 99)

        from processor.resolver import resolve_product
        result = await resolve_product("Completely Different Product XYZ")

        assert result.is_new is True
        assert result.master_product_id == 10
        assert result.similarity == 1.0
        mock_conn.fetchval.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_db_creates_first_product(self, mocker):
        """DB vacía (fetchrow=None) → crea el primer producto."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_conn.fetchval.return_value = 1
        _patch_resolver(mocker, mock_conn, 1)

        from processor.resolver import resolve_product
        result = await resolve_product("Sony WH-1000XM4")

        assert result.is_new is True
        assert result.master_product_id == 1
        mock_conn.fetchval.assert_called_once()

    @pytest.mark.asyncio
    async def test_borderline_085_matches(self, mocker):
        """EXACTAMENTE 0.85 → está en el threshold, DEBE matchear."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": 3, "canonical_name": "apple airpods pro", "similarity": 0.85,
        }
        _patch_resolver(mocker, mock_conn, 7)

        from processor.resolver import resolve_product
        result = await resolve_product("AirPods Pro 2")

        assert result.is_new is False
        assert result.master_product_id == 3
        assert result.similarity == 0.85
        mock_conn.fetchval.assert_not_called()

    @pytest.mark.asyncio
    async def test_borderline_084_creates_new(self, mocker):
        """0.84 → justo por debajo del threshold, DEBE crear nuevo."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": 3, "canonical_name": "apple airpods pro", "similarity": 0.84,
        }
        mock_conn.fetchval.return_value = 20
        _patch_resolver(mocker, mock_conn, 8)

        from processor.resolver import resolve_product
        result = await resolve_product("Something Almost Similar")

        assert result.is_new is True
        assert result.master_product_id == 20
        mock_conn.fetchval.assert_called_once()

    @pytest.mark.asyncio
    async def test_insert_receives_correct_brand_and_model(self, mocker):
        """Verifica que el INSERT pase brand y model correctos."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_conn.fetchval.return_value = 50
        _patch_resolver(mocker, mock_conn, 5)

        from processor.resolver import resolve_product
        result = await resolve_product("Sony WH-1000XM4 Wireless")

        assert result.is_new is True
        call_args = mock_conn.fetchval.call_args[0]
        sql = call_args[0]
        canonical_name = call_args[1]
        brand = call_args[2]
        model = call_args[3]

        assert "INSERT INTO master_products" in sql
        assert "sony" in canonical_name
        assert brand == "sony"
        assert model == "wh1000xm4"


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
