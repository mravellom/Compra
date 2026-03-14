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
    "bosch", "dyson", "arduino", "raspberry", "creality", "elegoo",
    "keychron", "steelseries", "rode", "shure", "focusrite", "behringer",
    "ugreen", "baseus", "aukey", "belkin", "satechi", "caldigit",
    "elgato", "fifine", "maono", "godox", "neewer", "manfrotto",
    "joby", "zhiyun", "osmo", "insta360", "tile", "chipolo",
})

# ── Pre-compiled regex patterns ──────────────────────────────
# Compiled once at module load, reused on every call.
_RE_SPECIAL_CHARS = re.compile(r"[^a-z0-9\s\-]")
_RE_DASH_ALPHA_NUM = re.compile(r"(?<=[a-z])-(?=[0-9])")
_RE_DASH_NUM_ALPHA = re.compile(r"(?<=[0-9])-(?=[a-z])")
_RE_MODEL_PATTERN = re.compile(r"^(?=[a-z]*\d)[a-z0-9]{3,}$")


def normalize_title(title: str) -> str:
    """Limpia un título de listing para matching."""
    # Unicode normalize + lowercase
    text = unicodedata.normalize("NFKD", title).lower()

    # Remover acentos
    text = "".join(c for c in text if not unicodedata.combining(c))

    # Remover caracteres especiales, conservar alfanuméricos y espacios
    text = _RE_SPECIAL_CHARS.sub(" ", text)

    # Normalizar guiones (wh-1000xm4 -> wh1000xm4)
    text = _RE_DASH_ALPHA_NUM.sub("", text)
    text = _RE_DASH_NUM_ALPHA.sub("", text)

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


KNOWN_MODELS = {
    # Headphones
    "wh1000xm4": "wh1000xm4",
    "wh1000xm5": "wh1000xm5",
    "wf1000xm4": "wf1000xm4",
    "wf1000xm5": "wf1000xm5",
    "airpods pro": "airpods-pro",
    "airpods max": "airpods-max",
    "airpods": "airpods",
    # Consoles
    "switch oled": "switch-oled",
    "switch lite": "switch-lite",
    "switch 2": "switch-2",
    "playstation 5": "ps5",
    "ps5": "ps5",
    "xbox series": "xbox-series",
    "pro controller": "pro-controller",
    # Phones
    "iphone 16": "iphone-16",
    "iphone 15": "iphone-15",
    "iphone 14": "iphone-14",
    "galaxy s24": "galaxy-s24",
    "galaxy s23": "galaxy-s23",
    "pixel 9": "pixel-9",
    "pixel 8": "pixel-8",
    # Peripherals
    "mx master 3": "mx-master-3",
    "mx master 3s": "mx-master-3s",
    "mx keys": "mx-keys",
    "magic keyboard": "magic-keyboard",
    "magic mouse": "magic-mouse",
    "magic trackpad": "magic-trackpad",
    # Trackers
    "airtag": "airtag",
    # Audio equipment
    "scarlett solo": "scarlett-solo",
    "scarlett 2i2": "scarlett-2i2",
    # Gadgets
    "raspberry pi": "raspberry-pi",
    # Cameras / Action
    "hero 12": "hero-12",
    "hero 13": "hero-13",
    "mavic mini": "mavic-mini",
    "mini 4": "mini-4",
    "osmo pocket": "osmo-pocket",
}


def extract_model(normalized_title: str, brand: str | None) -> str | None:
    """Extrae el modelo: primero busca modelos conocidos, luego patrones alfanuméricos."""
    # Check known model names first (handles models without numbers like "airpods pro")
    for pattern, model_id in KNOWN_MODELS.items():
        if pattern in normalized_title:
            return model_id

    tokens = normalized_title.split()

    # Filtrar la marca si existe
    if brand:
        tokens = [t for t in tokens if t != brand]

    # Un modelo suele tener letras + números (ej: wh1000xm4, a2236)
    candidates = [t for t in tokens if _RE_MODEL_PATTERN.match(t)]

    return candidates[0] if candidates else None


# ── Accessory / part detection ───────────────────────────────
ACCESSORY_KEYWORDS = frozenset({
    "funda", "case", "cover", "protector", "skin", "carcasa",
    "filtro", "filter", "repuesto", "replacement", "reemplazo",
    "cargador", "charger", "cable", "adaptador", "adapter",
    "soporte", "mount", "holder", "stand", "base", "dock",
    "montaje", "bracket", "montatura",
    "correa", "strap", "band", "banda", "pulsera",
    "almohadilla", "ear pad", "earpad", "cushion",
    "bateria", "battery", "pila",
    "vidrio", "glass", "mica", "pelicula", "film", "lamina",
    "bolsa", "bolso", "estuche", "pouch", "carrying",
    "llavero", "keychain", "keyring",
    "mando", "control remoto", "remote",
    "punta", "tip", "nozzle", "boquilla",
    "tornillo", "screw", "junta", "goma", "rubber", "seal",
})

# Keywords indicating a full/main product (not an accessory)
MAIN_PRODUCT_KEYWORDS = frozenset({
    "aspiradora", "vacuum", "audifonos", "headphones", "auriculares",
    "consola", "console", "laptop", "notebook", "tablet",
    "celular", "phone", "telefono", "smartphone",
    "camara", "camera", "proyector", "projector",
    "impresora", "printer", "monitor", "television", "tv",
    "bocina", "speaker", "altavoz", "parlante",
    "teclado mecanico", "mechanical keyboard",
    "reloj", "watch", "smartwatch",
    "drone", "dron",
})


def is_accessory(normalized_title: str) -> bool:
    """Detect if a listing is an accessory/part rather than a main product."""
    tokens = set(normalized_title.split())
    # If it contains main product keywords, it's likely a main product
    for kw in MAIN_PRODUCT_KEYWORDS:
        if kw in normalized_title:
            return False
    # Check for accessory keywords
    for kw in ACCESSORY_KEYWORDS:
        if kw in normalized_title:
            return True
    return False


def get_product_type(normalized_title: str) -> str:
    """Classify as 'accessory' or 'main' product."""
    return "accessory" if is_accessory(normalized_title) else "main"
