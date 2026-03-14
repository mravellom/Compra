import logging
import os

import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None

# Multilingual model: handles English, Spanish, Portuguese (and 50+ other languages).
# Output dimension: 384.
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

# ── Device selection ─────────────────────────────────────────
# Auto-detect GPU; override with EMBEDDING_DEVICE env var.
def _select_device() -> str:
    override = os.getenv("EMBEDDING_DEVICE", "").strip().lower()
    if override:
        return override
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"

DEVICE = _select_device()

# ── Batch size defaults ──────────────────────────────────────
# GPU can handle much larger batches; CPU is limited by RAM bandwidth.
DEFAULT_BATCH_SIZE_CPU = 64
DEFAULT_BATCH_SIZE_GPU = 256
EMBEDDING_BATCH_SIZE = int(os.getenv(
    "EMBEDDING_BATCH_SIZE",
    str(DEFAULT_BATCH_SIZE_GPU if DEVICE == "cuda" else DEFAULT_BATCH_SIZE_CPU),
))


def get_model() -> SentenceTransformer:
    """Load model (singleton, reused across calls)."""
    global _model
    if _model is None:
        logger.info("Loading sentence-transformers model '%s' on device='%s'...", MODEL_NAME, DEVICE)
        _model = SentenceTransformer(MODEL_NAME, device=DEVICE)
        logger.info("Model loaded (dimension: %d, device: %s)", _model.get_sentence_embedding_dimension(), DEVICE)
    return _model


def generate_embedding(text: str) -> list[float]:
    """Generate a 384-dim vector from a single text (legacy, single-item path)."""
    model = get_model()
    embedding: np.ndarray = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()


def generate_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Generate 384-dim vectors for a batch of texts.

    Uses the model's internal batching for maximum throughput.
    Returns a list of embedding vectors in the same order as input texts.
    """
    if not texts:
        return []
    model = get_model()
    embeddings: np.ndarray = model.encode(
        texts,
        normalize_embeddings=True,
        batch_size=EMBEDDING_BATCH_SIZE,
        show_progress_bar=False,
    )
    return embeddings.tolist()
