import logging

import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    """Carga el modelo (singleton, se reutiliza entre llamadas)."""
    global _model
    if _model is None:
        logger.info("Loading sentence-transformers model 'all-MiniLM-L6-v2'...")
        _model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
        logger.info("Model loaded (dimension: %d)", _model.get_sentence_embedding_dimension())
    return _model


def generate_embedding(text: str) -> list[float]:
    """Genera un vector de 384 dimensiones a partir de un texto."""
    model = get_model()
    embedding: np.ndarray = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()
