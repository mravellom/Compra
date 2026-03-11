import logging

import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None

# Multilingual model: handles English, Spanish, Portuguese (and 50+ other languages).
# Replaces all-MiniLM-L6-v2 which only supports English well.
# Output dimension: 384 (same as before — no schema/index changes needed).
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


def get_model() -> SentenceTransformer:
    """Carga el modelo (singleton, se reutiliza entre llamadas)."""
    global _model
    if _model is None:
        logger.info("Loading sentence-transformers model '%s'...", MODEL_NAME)
        _model = SentenceTransformer(MODEL_NAME, device="cpu")
        logger.info("Model loaded (dimension: %d)", _model.get_sentence_embedding_dimension())
    return _model


def generate_embedding(text: str) -> list[float]:
    """Genera un vector de 384 dimensiones a partir de un texto."""
    model = get_model()
    embedding: np.ndarray = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()
