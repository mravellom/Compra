"""
NLP-based product category classifier.
Matches product titles against keyword patterns to assign categories.
Shared between processor (asyncpg) and api (SQLAlchemy) contexts.
"""
import re

CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("Laptops", [
        r"\blaptop\b", r"\bnotebook\b", r"\bmacbook\b", r"\bchromebook\b",
        r"\bthinkpad\b", r"\bsurface\s+(?:pro|laptop|go)\b",
    ]),
    ("Tablets", [
        r"\bipad\b", r"\btablet\b", r"\bgalaxy\s+tab\b", r"\bfire\s+hd\b",
    ]),
    ("Smartphones", [
        r"\biphone\b", r"\bsmartphone\b", r"\bcelular\b", r"\bgalaxy\s+s\d",
        r"\bgalaxy\s+z\b", r"\bpixel\s+\d", r"\bmoto\s+g\b", r"\bredmi\b",
        r"\boneplus\b", r"\bxiaomi\b.*\b(?:note|pro|ultra)\b",
    ]),
    ("Smartwatches", [
        r"\bapple\s+watch\b", r"\bsmartwatch\b", r"\bgalaxy\s+watch\b",
        r"\bgarmin\b.*\bwatch\b", r"\bfitbit\b", r"\bamazfit\b",
    ]),
    ("Headphones", [
        r"\bheadphone\b", r"\bearphone\b", r"\bearbuds?\b", r"\bairpods?\b",
        r"\bwh.?1000", r"\bwf.?1000", r"\bbuds?\s+(?:pro|plus|live)\b",
        r"\baudifonos?\b", r"\bin.?ear\b", r"\bover.?ear\b",
    ]),
    ("Speakers", [
        r"\bspeaker\b", r"\bbocina\b", r"\bsoundbar\b", r"\bsubwoofer\b",
        r"\bhomepod\b", r"\becho\s+dot\b", r"\bsonos\b",
    ]),
    ("Gaming Consoles", [
        r"\bplaystation\b", r"\bps[45]\b", r"\bxbox\b", r"\bnintendo\s+switch\b",
        r"\bsteam\s+deck\b", r"\bconsola\b",
    ]),
    ("Gaming Accessories", [
        r"\bcontroller\b", r"\bcontrol\b.*\b(?:xbox|ps[45]|switch)\b",
        r"\bgaming\s+(?:mouse|keyboard|headset|chair)\b", r"\bjoystick\b",
    ]),
    ("Video Games", [
        r"\bjuego\b.*\b(?:ps[45]|xbox|switch)\b",
        r"\bgame\b.*\b(?:ps[45]|xbox|switch|pc)\b",
    ]),
    ("Cameras", [
        r"\bcamera\b", r"\bcamara\b", r"\bmirrorless\b", r"\bdslr\b",
        r"\bgopro\b", r"\baction\s+cam\b", r"\binstax\b",
    ]),
    ("Drones", [
        r"\bdrone\b", r"\bdji\b.*\b(?:mini|air|mavic|avata)\b",
    ]),
    ("TVs & Monitors", [
        r"\btv\b", r"\btelevisi[oó]n\b", r"\bmonitor\b", r"\boled\b",
        r"\bqled\b", r"\bsmart\s+tv\b", r"\bpantalla\b",
    ]),
    ("Smart Home", [
        r"\balexa\b", r"\becho\b", r"\bgoogle\s+(?:home|nest)\b",
        r"\bring\b.*\b(?:doorbell|camera)\b", r"\bthermostat\b",
    ]),
    ("GPUs", [
        r"\bgpu\b", r"\bgraphics\s+card\b", r"\brtx\s+\d", r"\bgtx\s+\d",
        r"\bradeon\s+rx\b", r"\btarjeta\s+(?:de\s+)?video\b",
    ]),
    ("CPUs & Components", [
        r"\bcpu\b", r"\bprocessor\b", r"\bprocesador\b",
        r"\bryzen\b", r"\bcore\s+i[3579]\b",
        r"\bram\b.*\b(?:ddr[45]|gb)\b", r"\bssd\b", r"\bnvme\b",
    ]),
    ("Phone Accessories", [
        r"\bcase\b.*\b(?:iphone|galaxy|phone)\b", r"\bfunda\b",
        r"\bscreen\s+protector\b", r"\bcharger\b", r"\bcargador\b",
        r"\bpower\s+bank\b",
    ]),
    ("Networking", [
        r"\brouter\b", r"\bwifi\b.*\b(?:mesh|extender|6e?)\b",
        r"\baccess\s+point\b", r"\bswitch\b.*\bports?\b",
    ]),
    ("Storage", [
        r"\bmicro\s*sd\b", r"\bsd\s+card\b", r"\busb\s+(?:drive|flash)\b",
        r"\bexternal\s+(?:hard|ssd|hdd)\b", r"\bdisco\s+duro\b",
    ]),
]

_COMPILED_RULES: list[tuple[str, list[re.Pattern]]] = [
    (cat, [re.compile(p, re.IGNORECASE) for p in patterns])
    for cat, patterns in CATEGORY_RULES
]


def classify_product(title: str) -> str:
    """Classify a product title into a category. Returns 'Other' if no match."""
    for category, patterns in _COMPILED_RULES:
        for pattern in patterns:
            if pattern.search(title):
                return category
    return "Other"
