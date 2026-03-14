"""
Search term expansion — generates variants for each base keyword.

Strategies:
1. Synonym mapping (manual, high-precision)
2. Prefix/suffix expansion (brand variations, connector types)
3. Spanish/English cross-language variants
"""
import logging
import re
from itertools import product as iterproduct

logger = logging.getLogger(__name__)

# ── Synonym groups ────────────────────────────────────────────
# Each key maps to a list of equivalent terms.
# When a base term contains a key, variants are generated with each synonym.
SYNONYM_MAP: dict[str, list[str]] = {
    "usb hub": ["usb-c hub", "type-c hub", "multiport usb hub", "docking station usb"],
    "usb-c": ["type-c", "usb type c", "thunderbolt"],
    "auriculares": ["audifonos", "earbuds", "headphones", "earphones"],
    "audifonos": ["auriculares", "earbuds", "headphones"],
    "parlante": ["bocina", "speaker", "altavoz"],
    "bocina": ["parlante", "speaker", "altavoz"],
    "cargador": ["charger", "cargador rapido", "fast charger"],
    "funda": ["case", "protector", "cover", "carcasa"],
    "teclado": ["keyboard", "teclado mecanico", "mechanical keyboard"],
    "mouse": ["raton", "mouse inalambrico", "wireless mouse"],
    "monitor": ["pantalla", "display", "screen"],
    "notebook": ["laptop", "portatil", "ultrabook"],
    "laptop": ["notebook", "portatil", "ultrabook"],
    "tablet": ["tableta", "ipad", "tab"],
    "smartwatch": ["reloj inteligente", "smart watch", "fitness tracker"],
    "drone": ["dron", "quadcopter", "mini drone"],
    "camara": ["camera", "camara digital", "cam"],
    "ssd": ["disco solido", "solid state drive", "nvme"],
    "disco duro": ["hard drive", "hdd", "disco externo"],
    "router": ["access point", "mesh wifi", "repetidor wifi"],
    "power bank": ["bateria portatil", "cargador portatil", "powerbank"],
    "protector pantalla": ["screen protector", "vidrio templado", "tempered glass"],
    "control": ["gamepad", "joystick", "mando"],
    "consola": ["console", "gaming console"],
    "memoria": ["ram", "memory", "memoria ram"],
    "tarjeta grafica": ["gpu", "graphics card", "video card"],
    "impresora": ["printer", "impresora laser", "impresora tinta"],
    "cable": ["cable usb", "cable hdmi", "cable lightning", "cable tipo c"],
}

# ── Brand + product type expansions ───────────────────────────
# When a brand is detected, generate "{brand} {product}" variants
BRAND_PRODUCT_MAP: dict[str, list[str]] = {
    "apple": ["airpods", "iphone", "ipad", "macbook", "apple watch", "airtag"],
    "samsung": ["galaxy", "galaxy buds", "galaxy watch", "galaxy tab"],
    "sony": ["wh-1000xm5", "wh-1000xm4", "wf-1000xm5", "playstation", "ps5"],
    "xiaomi": ["redmi", "poco", "mi band", "mi watch"],
    "jbl": ["flip", "charge", "tune", "go", "xtreme", "pulse"],
    "logitech": ["g pro", "mx master", "g502", "g305", "brio"],
    "razer": ["deathadder", "viper", "blackwidow", "kraken"],
    "nintendo": ["switch", "switch oled", "joy-con", "pro controller"],
    "bose": ["quietcomfort", "soundlink", "sport earbuds"],
    "anker": ["soundcore", "powercore", "nano", "735 charger"],
    "marshall": ["major", "minor", "emberton", "stanmore"],
    "hyperx": ["cloud", "alloy", "pulsefire"],
    "steelseries": ["arctis", "rival", "apex"],
    "corsair": ["k70", "void", "hs80", "vengeance"],
    "tp-link": ["deco", "archer", "tapo"],
    "google": ["pixel", "chromecast", "nest"],
    "huawei": ["freebuds", "watch gt", "matepad"],
    "lenovo": ["thinkpad", "ideapad", "legion", "tab"],
    "asus": ["rog", "zenbook", "vivobook", "tuf"],
    "dji": ["mini", "mavic", "air", "osmo"],
}

# ── Modifier expansions ──────────────────────────────────────
# Generic modifiers appended to base terms
MODIFIERS: list[str] = [
    "inalambrico",
    "bluetooth",
    "wifi",
    "pro",
    "mini",
    "portatil",
    "profesional",
    "gamer",
    "gaming",
]

# ── Price-range qualifiers ────────────────────────────────────
# These help find products in specific price tiers (high-margin targets)
PRICE_QUALIFIERS: list[str] = [
    "premium",
    "barato",
    "oferta",
    "original",
]


def expand_term(term: str, max_variants: int = 8) -> list[str]:
    """
    Expand a single search term into related variants.

    Returns the original term plus up to max_variants expansions.
    """
    term_lower = term.lower().strip()
    variants: set[str] = {term_lower}

    # 1. Direct synonym matches
    for key, synonyms in SYNONYM_MAP.items():
        if key in term_lower:
            for syn in synonyms:
                expanded = term_lower.replace(key, syn)
                variants.add(expanded)
                if len(variants) >= max_variants + 1:
                    break

    # 2. Brand expansion — if term is a brand name, add brand+product combos
    for brand, products in BRAND_PRODUCT_MAP.items():
        if term_lower == brand or term_lower.startswith(brand + " "):
            for prod in products:
                variants.add(f"{brand} {prod}")
                if len(variants) >= max_variants + 1:
                    break

    # 3. If term contains a brand, try adding modifiers
    for brand in BRAND_PRODUCT_MAP:
        if brand in term_lower:
            for mod in MODIFIERS[:3]:
                variants.add(f"{term_lower} {mod}")
                if len(variants) >= max_variants + 1:
                    break
            break

    # 4. Generic modifier expansion (only if we still have room)
    if len(variants) < max_variants + 1:
        # Only add modifiers that aren't already in the term
        for mod in MODIFIERS:
            if mod not in term_lower:
                variants.add(f"{term_lower} {mod}")
                if len(variants) >= max_variants + 1:
                    break

    result = list(variants)[:max_variants + 1]
    return result


def expand_all_terms(
    base_terms: list[str],
    max_variants_per_term: int = 6,
) -> list[str]:
    """
    Expand a list of search terms into a larger set of variants.

    Args:
        base_terms: Original search terms.
        max_variants_per_term: Max variants per base term (including original).

    Returns:
        Deduplicated list of all expanded terms.
    """
    all_terms: list[str] = []
    seen: set[str] = set()

    for term in base_terms:
        expanded = expand_term(term, max_variants=max_variants_per_term)
        for t in expanded:
            normalized = t.lower().strip()
            if normalized not in seen:
                seen.add(normalized)
                all_terms.append(t)

    logger.info(
        "Keyword expansion: %d base terms → %d expanded terms (%.1fx)",
        len(base_terms), len(all_terms), len(all_terms) / max(len(base_terms), 1),
    )
    return all_terms


# ── High-value arbitrage terms ────────────────────────────────
# Terms specifically chosen for cross-marketplace price differentials
ARBITRAGE_SEARCH_TERMS: list[str] = [
    # Audio
    "airpods pro", "airpods max", "sony wh-1000xm5", "jbl flip 6",
    "jbl charge 5", "marshall major", "bose quietcomfort",
    # Phones
    "iphone 15", "iphone 14", "samsung galaxy s24", "xiaomi redmi note",
    "google pixel", "samsung galaxy buds",
    # Computing
    "macbook air", "thinkpad", "logitech mx master", "razer deathadder",
    "corsair k70", "ssd nvme 1tb", "ram ddr5",
    # Gaming
    "nintendo switch oled", "ps5 controller", "xbox controller",
    "nintendo joy-con", "steam deck",
    # Cameras & Drones
    "gopro hero", "dji mini", "dji mavic",
    # Wearables
    "apple watch", "samsung galaxy watch", "garmin", "fitbit",
    # Smart Home
    "alexa echo", "google nest", "ring doorbell", "tp-link tapo",
    # Storage
    "sandisk 128gb", "samsung evo", "wd passport",
    # Accessories
    "anker charger", "power bank 20000", "cable usb-c",
    "protector iphone", "funda samsung",
    # Tools
    "dremel", "bosch professional", "makita",
]
