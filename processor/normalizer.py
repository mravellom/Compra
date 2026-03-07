import re
import unicodedata

STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "for", "of", "to", "in", "on", "with",
    "is", "it", "by", "at", "from", "as", "be", "was", "are", "been",
    "new", "brand", "sealed", "box", "free", "shipping", "fast",
    "lot", "bundle", "set", "pack", "authentic", "genuine", "original",
    "oem", "usa", "us", "seller", "ships", "condition", "mint",
    "excellent", "good", "great", "like", "used", "pre", "owned",
    "tested", "working", "read", "description", "see", "pics", "photos",
    "sale", "deal", "cheap", "best", "offer", "buy", "now",
})

# Marcas conocidas para extracción
KNOWN_BRANDS = frozenset({
    "sony", "apple", "samsung", "bose", "nintendo", "microsoft", "google",
    "lg", "jbl", "sennheiser", "logitech", "razer", "corsair", "asus",
    "dell", "hp", "lenovo", "acer", "msi", "gigabyte", "amd", "intel",
    "nvidia", "canon", "nikon", "fujifilm", "gopro", "dji", "garmin",
    "fitbit", "xiaomi", "huawei", "oneplus", "motorola", "beats",
    "skullcandy", "anker", "jabra", "plantronics", "hyperx",
})


def normalize_title(title: str) -> str:
    """Limpia un título de listing para matching."""
    # Unicode normalize + lowercase
    text = unicodedata.normalize("NFKD", title).lower()

    # Remover acentos
    text = "".join(c for c in text if not unicodedata.combining(c))

    # Remover caracteres especiales, conservar alfanuméricos y espacios
    text = re.sub(r"[^a-z0-9\s\-]", " ", text)

    # Normalizar guiones (wh-1000xm4 -> wh1000xm4)
    text = re.sub(r"(?<=[a-z])-(?=[0-9])", "", text)
    text = re.sub(r"(?<=[0-9])-(?=[a-z])", "", text)

    # Tokenizar y filtrar stop words
    tokens = text.split()
    tokens = [t for t in tokens if t not in STOP_WORDS and len(t) > 1]

    return " ".join(tokens)


def extract_brand(normalized_title: str) -> str | None:
    """Intenta extraer la marca del título normalizado."""
    tokens = normalized_title.split()
    for token in tokens:
        if token in KNOWN_BRANDS:
            return token
    return None


def extract_model(normalized_title: str, brand: str | None) -> str | None:
    """Extrae el modelo: tokens alfanuméricos que parecen números de modelo."""
    tokens = normalized_title.split()

    # Filtrar la marca si existe
    if brand:
        tokens = [t for t in tokens if t != brand]

    # Un modelo suele tener letras + números (ej: wh1000xm4, a2236)
    model_pattern = re.compile(r"^(?=[a-z]*\d)[a-z0-9]{3,}$")
    candidates = [t for t in tokens if model_pattern.match(t)]

    return candidates[0] if candidates else None
