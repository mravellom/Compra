"""
Advanced Arbitrage Detection Engine v3.

Key changes from v2 → v3:
- Soft-filter model: most checks become scoring penalties, not hard rejections
- Relaxed hard thresholds to match real reseller conditions (3% margin, $3 profit)
- 48h freshness window instead of 24h
- Higher MAX_ROI (500%) and MAX_PRICE_RATIO (8x) with scoring penalties
- Seller trust is a soft signal: rating < 1.5 is hard reject, < 3.0 is risk penalty
- Cross-border risk scaled down to avoid over-penalizing international routes

Algorithm:
1. Fetch exchange rates (cached 1h TTL)
2. SQL: find products with listings in 2+ marketplaces (48h window)
3. For each product cluster:
   a. Convert all prices to USD
   b. Remove statistical outliers (IQR method)
   c. Hard-reject sellers with rating < 1.5
   d. Check variant/condition compatibility
   e. For each (buy_mp, sell_mp) pair:
      - Buy price = cheapest on buy_mp
      - Sell price = median on sell_mp * 0.97 (undercut)
      - Calculate all fees, shipping, import tax
      - Net profit = sell - buy - all_costs
      - Hard-reject only if profit < $3, margin < 3%, ROI < 2%
      - Apply soft penalties for thin margins, high ratios, low trust
   f. Keep best opportunity per product (highest score)
4. Upsert: update existing active opps, create new ones, expire stale
5. Score 0-100 composite with soft penalties, rank by score
"""
import asyncio
import logging
import math
import os
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .currency import get_rates, to_usd
from .database import async_session
from .models import MasterProduct, Opportunity, PriceHistory, ProductListing
from .routes_config import (
    ARBITRAGE_ROUTES,
    VALID_ROUTE_PAIRS,
    MARKETPLACE_FEES,
    CROSS_BORDER_FEES,
    DEFAULT_CROSS_BORDER,
    get_cross_border_fees,
    get_route_label,
    get_route_difficulty,
    is_cross_border,
)
from .scoring import ScoringInput, score as score_opportunity_v2

logger = logging.getLogger(__name__)


import re as _re

# Model number patterns — capture identifiers that distinguish product versions
_MODEL_PATTERNS = [
    # Phone models: iPhone 17e, iPhone 17 Pro, iPhone 15 Pro Max, Galaxy S25 Ultra
    _re.compile(r'(iphone\s*\d+\s*(?:pro\s*max|pro|plus|e|mini)?)', _re.I),
    _re.compile(r'(galaxy\s*(?:s|a|z|tab|watch|buds)\s*\d+\s*(?:ultra|plus|fe|pro|lite|fold|flip)?(?:\s*\d+)?)', _re.I),
    _re.compile(r'(pixel\s*\d+\s*(?:pro\s*xl|pro\s*fold|pro|a)?)', _re.I),
    _re.compile(r'(redmi\s*(?:note\s*)?\d+\s*(?:pro\s*max|pro\s*plus|pro|s|c|a)?)', _re.I),
    _re.compile(r'(poco\s*(?:m|x|f|c|pad)\s*\d*\s*(?:pro|gt)?)', _re.I),
    # Watch models: Watch SE, Watch Series 7, Watch Series 10, Watch Ultra
    _re.compile(r'(watch\s*(?:series\s*\d+|ultra\s*\d*|se\s*\d*)\b)', _re.I),
    # Headphone models: Freebuds SE 2, Freebuds 7i, AirPods Pro, WH-1000XM5
    _re.compile(r'(freebuds\s*\w+\s*\d*)', _re.I),
    _re.compile(r'(airpods\s*(?:pro|max)?\s*\d*)', _re.I),
    _re.compile(r'([a-z]{2,3}-?\d{3,4}[a-z]*\d*)', _re.I),  # WH-1000XM5, XM4, etc.
    # RAM: DDR4 vs DDR5 + capacity (e.g. "ddr5 16gb", "ddr4 8gb")
    _re.compile(r'(ddr\d\s*\d+\s*gb)', _re.I),
    # GoPro models
    _re.compile(r'(gopro\s*hero\s*\d*\s*(?:black|silver|white|lit)?)', _re.I),
    # Gaming peripherals with version: Blackwidow V3, V4 Pro, V4 75%, Kraken V4, etc.
    _re.compile(r'((?:blackwidow|kraken|deathadder|viper|huntsman|basilisk|ornata)\s*v\d+\s*(?:pro|lite|mini|te|75%|tkl)?)', _re.I),
    # Laptop models
    _re.compile(r'((?:tuf|rog|ally|thinkpad|latitude|xps)\s*\w*\s*\w*)', _re.I),
    # Tab models
    _re.compile(r'(tab\s*(?:s|a)?\s*\d+\s*(?:fe|lite|ultra|plus)?)', _re.I),
]


def _extract_model_id(title: str) -> str | None:
    """Extract the specific model identifier from a product title."""
    if not title:
        return None
    for pat in _MODEL_PATTERNS:
        m = pat.search(title)
        if m:
            # Normalize whitespace and case
            return _re.sub(r'\s+', ' ', m.group(1).lower().strip())
    return None


def _titles_match(title_a: str | None, title_b: str | None) -> bool:
    """Check if two normalized titles refer to the same product.

    Extracts model identifiers (e.g. 'iphone 17 pro', 'galaxy s25 ultra')
    and compares them. If both have a model ID, they must match exactly.
    Falls back to Jaccard token overlap if no model ID can be extracted.
    """
    if not title_a or not title_b:
        return True

    # Try model-based matching first
    model_a = _extract_model_id(title_a)
    model_b = _extract_model_id(title_b)

    if model_a and model_b:
        return model_a == model_b

    # If only one has a model ID, they're likely different products
    if bool(model_a) != bool(model_b):
        return False

    # Fallback: Jaccard token overlap (neither has model ID)
    tokens_a = set(title_a.lower().split())
    tokens_b = set(title_b.lower().split())

    stop = {"de", "para", "con", "en", "el", "la", "los", "las", "un", "una",
            "del", "al", "y", "o", "a", "e", "the", "for", "with", "and", "in",
            "color", "negro", "blanco", "gris", "azul", "rojo", "black", "white",
            "grey", "blue", "red", "green", "pink", "silver", "gold"}
    tokens_a = {t for t in tokens_a if len(t) > 1 and t not in stop}
    tokens_b = {t for t in tokens_b if len(t) > 1 and t not in stop}

    if not tokens_a or not tokens_b:
        return True

    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    jaccard = len(intersection) / len(union)

    return jaccard >= 0.25


# ── Configurable thresholds (v3: relaxed hard filters) ────
MIN_PROFIT_USD = float(os.getenv("MIN_PROFIT_USD", "1"))
MIN_MARGIN = float(os.getenv("MIN_MARGIN", "0.01"))       # 1% — soft penalties below 8%
MIN_ROI = float(os.getenv("MIN_ROI", "0.01"))             # 1%
MAX_ROI = float(os.getenv("MAX_ROI", "5.0"))              # 500% — allowed but penalized above 200%
MAX_PRICE_RATIO = float(os.getenv("MAX_PRICE_RATIO", "8.0"))  # 8x — allowed but penalized above 6x
MIN_SELLER_RATING = float(os.getenv("MIN_SELLER_RATING", "1.5"))  # hard floor
STALE_HOURS = int(os.getenv("STALE_HOURS", "48"))         # 48h freshness window

# ── Data structures ───────────────────────────────────────
@dataclass
class ProfitCalc:
    buy_price_usd: float
    sell_price_usd: float
    marketplace_fee: float
    payment_fee: float
    sell_tax: float
    import_tax: float
    domestic_shipping: float
    international_shipping: float
    total_fees: float
    net_profit: float
    roi: float
    margin: float  # net_profit / sell_price


@dataclass
class CompetitionInfo:
    score: float
    competitor_count: int
    lowest_price_usd: float
    avg_price_usd: float
    median_price_usd: float
    realistic_sell_usd: float


# ── Core calculations ─────────────────────────────────────
def calculate_profit(
    buy_usd: float,
    sell_usd: float,
    buy_mp: str,
    sell_mp: str,
    buy_free_ship: bool = False,
    sell_free_ship: bool = False,
) -> ProfitCalc:
    """Full profit calculation with all real-world costs."""
    sell_fees = MARKETPLACE_FEES.get(sell_mp, MARKETPLACE_FEES["ebay"])

    marketplace_fee = sell_usd * sell_fees["commission"]
    payment_processing_rate = sell_fees.get("payment_processing", 0)
    payment_fee = sell_usd * payment_processing_rate
    if payment_processing_rate > 0:
        payment_fee += 0.30  # Fixed fee only for marketplaces with separate payment processing
    sell_tax = sell_usd * sell_fees.get("vat_rate", 0.0)
    domestic_shipping = 0.0 if sell_free_ship else sell_fees.get("domestic_shipping", 5.0)

    international_shipping = 0.0
    import_tax = 0.0
    if is_cross_border(buy_mp, sell_mp):
        cb_fees = get_cross_border_fees(buy_mp, sell_mp)
        international_shipping = cb_fees["shipping_usd"]
        # CIF = Cost + Insurance + Freight
        insurance = buy_usd * cb_fees.get("insurance_rate", 0.02)
        cif_value = buy_usd + insurance + international_shipping
        import_tax = cif_value * cb_fees["import_tax_rate"]

    total_fees = marketplace_fee + payment_fee + sell_tax + import_tax + domestic_shipping + international_shipping
    net_profit = sell_usd - buy_usd - total_fees
    roi = net_profit / buy_usd if buy_usd > 0 else 0.0
    margin = net_profit / sell_usd if sell_usd > 0 else 0.0

    return ProfitCalc(
        buy_price_usd=round(buy_usd, 2),
        sell_price_usd=round(sell_usd, 2),
        marketplace_fee=round(marketplace_fee, 2),
        payment_fee=round(payment_fee, 2),
        sell_tax=round(sell_tax, 2),
        import_tax=round(import_tax, 2),
        domestic_shipping=round(domestic_shipping, 2),
        international_shipping=round(international_shipping, 2),
        total_fees=round(total_fees, 2),
        net_profit=round(net_profit, 2),
        roi=round(roi, 4),
        margin=round(margin, 4),
    )


# ── Filtering ─────────────────────────────────────────────
def is_outlier_price(price: float, prices: list[float]) -> bool:
    """IQR-based outlier detection using proper quartile calculation."""
    if len(prices) < 4:
        return False
    q1, _, q3 = statistics.quantiles(prices, n=4)
    iqr = q3 - q1
    return price < (q1 - 1.5 * iqr) or price > (q3 + 1.5 * iqr)


def is_trustworthy(listing: ProductListing) -> bool:
    """Hard reject only extremely untrustworthy sellers (< 1.5).
    Low-but-passable ratings (1.5-3.0) are handled as scoring penalties."""
    if listing.seller_rating is not None and listing.seller_rating < MIN_SELLER_RATING:
        return False
    return True


VARIANT_STORAGE = ("64gb", "128gb", "256gb", "512gb", "1tb", "2tb")
VARIANT_CAPACITY = ("5000mah", "10000mah", "20000mah", "25000mah", "30000mah",
                    "40w", "65w", "100w", "140w")
BUNDLE_KEYWORDS = ("bundle", "combo", "kit", "pack", "set", "con funda", "con case")
ACCESSORY_KEYWORDS = (
    "funda", "case", "cover", "protector", "skin", "carcasa",
    "filtro", "filter", "repuesto", "replacement", "reemplazo",
    "cable", "adaptador", "adapter", "soporte", "mount", "holder",
    "montaje", "montatura", "bracket",
    "correa", "strap", "band", "almohadilla", "ear pad", "cushion",
    "vidrio", "glass", "mica", "pelicula", "film",
    "bolsa", "estuche", "pouch", "llavero", "punta", "tip",
    "tornillo", "junta", "goma", "rubber", "seal", "cargador", "charger",
)
MAIN_PRODUCT_KEYWORDS = (
    "aspiradora", "vacuum", "audifonos", "headphones", "auriculares",
    "consola", "console", "laptop", "tablet", "celular", "phone",
    "camara", "camera", "proyector", "impresora", "monitor", "tv",
    "bocina", "speaker", "altavoz", "parlante", "teclado mecanico",
    "reloj", "watch", "smartwatch", "drone", "dron",
)


def _is_accessory(title_lower: str) -> bool:
    """Check if a title refers to an accessory/part."""
    # Accessory keywords take priority — "funda para auriculares" is an accessory
    is_acc = any(kw in title_lower for kw in ACCESSORY_KEYWORDS)
    if not is_acc:
        return False
    # "funda para auriculares" → accessory; but "auriculares con funda" → not accessory
    # Heuristic: if accessory keyword appears before main product keyword, it's an accessory
    acc_pos = min((title_lower.index(kw) for kw in ACCESSORY_KEYWORDS if kw in title_lower), default=999)
    main_pos = min((title_lower.index(kw) for kw in MAIN_PRODUCT_KEYWORDS if kw in title_lower), default=999)
    if main_pos == 999:
        return True  # No main product keyword, just accessory
    return acc_pos < main_pos


def _extract_model_numbers(title: str) -> set[str]:
    """Extract model identifiers like 'edge 540', 'wh-1000xm4', 'tank 720'."""
    import re
    # Match patterns: letters+numbers (wh1000xm4), numbers after brand words (edge 540)
    patterns = re.findall(r'\b([a-z]+[\-]?\d{2,}[a-z0-9]*)\b', title)
    # Match standalone model numbers after known product words
    model_words = re.findall(
        r'\b(?:edge|hero|mini|v|series|pro|wh|wf|xm|tank|tab|redmi|note|'
        r'pad|watch|buds|band|ecotank|pixma|envy|deskjet|laserjet|'
        r'l|gt|rtx|rx|ryzen|core\s*i)\s*(\d{2,}[a-z0-9]*)\b',
        title,
    )
    return set(p.replace("-", "") for p in patterns) | set(model_words)


def variants_compatible(buy: ProductListing, sell: ProductListing) -> bool:
    """Prevent storage/bundle/accessory/model/condition mismatches."""
    # Hard rule: conditions must match exactly (new ≠ used ≠ refurbished)
    if buy.condition != sell.condition:
        return False

    b, s = buy.title.lower(), sell.title.lower()

    # Refurbished detection from title (scrapers may miss condition)
    _refurb_keywords = ("reacond", "refurbish", "renewed", "remanufactur", "renovado")
    b_refurb = any(k in b for k in _refurb_keywords)
    s_refurb = any(k in s for k in _refurb_keywords)
    if b_refurb != s_refurb:
        return False

    # Accessory vs main product mismatch
    b_acc = _is_accessory(b)
    s_acc = _is_accessory(s)
    if b_acc != s_acc:
        return False

    # Model number mismatch (e.g. Edge 540 vs Edge 1040, WH-1000XM4 vs WF-1000XM4)
    b_models = _extract_model_numbers(b)
    s_models = _extract_model_numbers(s)
    if b_models and s_models and not b_models.intersection(s_models):
        # Both have model numbers but none in common — likely different products
        logger.debug("Model mismatch: %s vs %s", b_models, s_models)
        return False

    # Storage mismatch (normalize "256 gb" → "256gb")
    b_norm = b.replace(" gb", "gb").replace(" tb", "tb")
    s_norm = s.replace(" gb", "gb").replace(" tb", "tb")
    b_storage = next((v for v in VARIANT_STORAGE if v in b_norm), None)
    s_storage = next((v for v in VARIANT_STORAGE if v in s_norm), None)
    if b_storage and s_storage and b_storage != s_storage:
        return False

    # Capacity mismatch (power banks, chargers)
    b_cap = next((v for v in VARIANT_CAPACITY if v in b.replace(" ", "")), None)
    s_cap = next((v for v in VARIANT_CAPACITY if v in s.replace(" ", "")), None)
    if b_cap and s_cap and b_cap != s_cap:
        return False

    # Console edition mismatch: "disco"/"disc" vs "digital"/"edición digital"
    b_has_disc = any(k in b for k in ("unidad de disco", "disc edition", "con disco", "con lector"))
    s_has_disc = any(k in s for k in ("unidad de disco", "disc edition", "con disco", "con lector"))
    b_digital_only = any(k in b for k in ("edición digital", "edicion digital", "digital edition")) and not b_has_disc
    s_digital_only = any(k in s for k in ("edición digital", "edicion digital", "digital edition")) and not s_has_disc
    if b_has_disc != s_has_disc or b_digital_only != s_digital_only:
        return False

    # Bundle mismatch
    b_bundle = any(k in b for k in BUNDLE_KEYWORDS)
    s_bundle = any(k in s for k in BUNDLE_KEYWORDS)
    if b_bundle != s_bundle:
        return False

    # "para ipad" vs "para imac" — different target device
    import re
    b_target = re.findall(r'(?:para\s+(?:el\s+)?)(ipad|imac|iphone|macbook|mac\b)', b)
    s_target = re.findall(r'(?:para\s+(?:el\s+)?)(ipad|imac|iphone|macbook|mac\b)', s)
    if b_target and s_target and set(b_target) != set(s_target):
        return False

    return True


# ── Competition ───────────────────────────────────────────
def analyze_competition(
    listings: list[ProductListing], rates: dict[str, float]
) -> CompetitionInfo:
    """Analyze sell-side competition. Returns realistic sell price (median * 0.97)."""
    if not listings:
        return CompetitionInfo(0, 0, 0, 0, 0, 0)

    prices = sorted(to_usd(float(l.price), l.currency, rates) for l in listings)
    n = len(prices)
    lowest = prices[0]
    avg = statistics.mean(prices)
    median = statistics.median(prices)
    realistic = median * 0.97  # 3% undercut

    if n <= 1:
        score = 10.0
    elif n <= 3:
        score = 30.0
    elif n <= 7:
        score = 55.0
    elif n <= 15:
        score = 75.0
    else:
        score = 90.0

    # Tight spread = harder to differentiate
    if n > 1:
        spread = (prices[-1] - prices[0]) / avg if avg > 0 else 0
        if spread < 0.05:
            score = min(100, score + 10)

    return CompetitionInfo(
        score=round(score, 1),
        competitor_count=n,
        lowest_price_usd=round(lowest, 2),
        avg_price_usd=round(avg, 2),
        median_price_usd=round(median, 2),
        realistic_sell_usd=round(realistic, 2),
    )


# ── Scoring ───────────────────────────────────────────────
def compute_velocity(listings: list[ProductListing]) -> float:
    """Sales velocity score 0-100 from reviews + sales + ratings."""
    if not listings:
        return 0.0

    total_reviews = sum(l.reviews_count or 0 for l in listings)
    total_sales = sum(l.sales_count or 0 for l in listings)
    ratings = [l.seller_rating for l in listings if l.seller_rating]
    avg_rating = statistics.mean(ratings) if ratings else 0.0
    n = max(len(listings), 1)

    review_score = min(100, (total_reviews / n) * 2)
    sales_score = min(100, (total_sales / n) * 1.5)
    rating_score = (avg_rating / 5.0) * 100 if avg_rating > 0 else 30.0

    return round(review_score * 0.4 + sales_score * 0.4 + rating_score * 0.2, 1)


async def compute_stability(db: AsyncSession, url: str) -> float:
    """Price stability score 0-100 from coefficient of variation."""
    result = await db.execute(
        select(PriceHistory.price)
        .where(PriceHistory.listing_url == url)
        .order_by(PriceHistory.recorded_at.desc())
        .limit(20)
    )
    prices = [float(r[0]) for r in result.all()]
    if len(prices) < 2:
        return 50.0

    avg = statistics.mean(prices)
    if avg == 0:
        return 50.0

    cv = statistics.stdev(prices) / avg
    return round(max(0, min(100, (1 - cv / 0.3) * 100)), 1)


async def compute_depth(
    db: AsyncSession, listings: list[ProductListing], competitor_count: int
) -> tuple[float, float, float, str]:
    """Returns (score, daily_sales, monthly_sales, scalability)."""
    if not listings:
        return 0.0, 0.0, 0.0, "low"

    estimates: list[float] = []
    for l in listings:
        age = await _listing_age(db, l.url)
        if l.sales_count and l.sales_count > 0 and age > 0:
            estimates.append(l.sales_count / age)
        if l.reviews_count and l.reviews_count > 0 and age > 0:
            estimates.append((l.reviews_count / 0.03) / max(age, 1))

    multiplier = 1.5 if competitor_count >= 10 else 1.2 if competitor_count >= 5 else 1.0
    daily = (statistics.median(estimates) * multiplier) if estimates else max(0.1, competitor_count / 3.0)
    monthly = round(daily * 30, 1)
    daily = round(daily, 2)

    score = min(100, 20 + 25 * math.log2(daily + 1)) if daily > 0 else 0.0
    score = round(score, 1)

    level = "high" if score >= 70 else "medium" if score >= 40 else "low"
    return score, daily, monthly, level


async def _listing_age(db: AsyncSession, url: str) -> float:
    result = await db.execute(
        select(func.min(PriceHistory.recorded_at)).where(PriceHistory.listing_url == url)
    )
    first = result.scalar()
    if not first:
        return 0.0
    delta = (datetime.now(timezone.utc) - first).total_seconds() / 86400.0
    return max(delta, 1.0)


# ── Bulk prefetch for scoring signals (eliminates N+1) ────

async def _prefetch_scoring_data(
    db: AsyncSession, listings: list,
) -> tuple[dict[str, list[float]], dict[str, float]]:
    """Bulk-fetch price histories and listing ages for all URLs in 2 queries.

    Returns:
        price_histories: url -> list of recent prices (max 20, desc order)
        listing_ages: url -> age in days (min 1.0)
    """
    urls = list({str(l.url) for l in listings})
    if not urls:
        return {}, {}

    # Query 1: price histories (all URLs, last 20 per URL)
    rows = (await db.execute(
        text("""
            SELECT listing_url, price::float
            FROM (
                SELECT listing_url, price,
                       ROW_NUMBER() OVER (PARTITION BY listing_url ORDER BY recorded_at DESC) AS rn
                FROM price_history
                WHERE listing_url = ANY(:urls)
            ) sub
            WHERE rn <= 20
        """),
        {"urls": urls},
    )).all()

    price_histories: dict[str, list[float]] = {}
    for url, price in rows:
        price_histories.setdefault(url, []).append(price)

    # Query 2: listing ages (MIN recorded_at per URL)
    age_rows = (await db.execute(
        text("""
            SELECT listing_url, MIN(recorded_at) AS first_seen
            FROM price_history
            WHERE listing_url = ANY(:urls)
            GROUP BY listing_url
        """),
        {"urls": urls},
    )).all()

    now = datetime.now(timezone.utc)
    listing_ages: dict[str, float] = {}
    for url, first_seen in age_rows:
        if first_seen:
            delta = (now - first_seen).total_seconds() / 86400.0
            listing_ages[url] = max(delta, 1.0)

    return price_histories, listing_ages


def _compute_stability_cached(prices: list[float]) -> float:
    """Price stability 0-100 from CV, using prefetched prices."""
    if len(prices) < 2:
        return 50.0
    avg = statistics.mean(prices)
    if avg == 0:
        return 50.0
    cv = statistics.stdev(prices) / avg
    return round(max(0, min(100, (1 - cv / 0.3) * 100)), 1)


def _compute_depth_cached(
    listings: list, competitor_count: int,
    age_cache: dict[str, float],
) -> tuple[float, float, float, str]:
    """Market depth using prefetched listing ages."""
    if not listings:
        return 0.0, 0.0, 0.0, "low"

    estimates: list[float] = []
    for l in listings:
        age = age_cache.get(str(l.url), 1.0)
        if l.sales_count and l.sales_count > 0 and age > 0:
            estimates.append(l.sales_count / age)
        if l.reviews_count and l.reviews_count > 0 and age > 0:
            estimates.append((l.reviews_count / 0.03) / max(age, 1))

    multiplier = 1.5 if competitor_count >= 10 else 1.2 if competitor_count >= 5 else 1.0
    daily = (statistics.median(estimates) * multiplier) if estimates else max(0.1, competitor_count / 3.0)
    monthly = round(daily * 30, 1)
    daily = round(daily, 2)

    score = min(100, 20 + 25 * math.log2(daily + 1)) if daily > 0 else 0.0
    score = round(score, 1)

    level = "high" if score >= 70 else "medium" if score >= 40 else "low"
    return score, daily, monthly, level


# Scoring weights (sum = 1.0)
W = {"profit": 0.20, "roi": 0.15, "margin": 0.10, "velocity": 0.15,
     "competition": 0.10, "stability": 0.10, "depth": 0.10, "demand": 0.10}


def score_opportunity(
    profit: float, roi: float, margin: float, velocity: float,
    competition: float, stability: float, depth: float, demand: float = 50.0,
) -> tuple[float, str]:
    """Composite score 0-100 and confidence level."""
    profit_n = min(100, (profit / 200) * 100) if profit > 0 else 0
    roi_n = min(100, roi * 100) if roi > 0 else 0
    margin_n = min(100, margin * 200) if margin > 0 else 0  # 50% margin = 100
    comp_n = max(0, 100 - competition)

    score = (
        profit_n * W["profit"] + roi_n * W["roi"] + margin_n * W["margin"]
        + velocity * W["velocity"] + comp_n * W["competition"]
        + stability * W["stability"] + depth * W["depth"] + demand * W["demand"]
    )
    score = round(min(100, max(0, score)), 1)
    confidence = "high" if score >= 75 else "medium" if score >= 50 else "low"
    return score, confidence


# ── Main pipeline ─────────────────────────────────────────
async def detect_opportunities(db: AsyncSession) -> list[Opportunity]:
    """
    Production arbitrage detection with upsert-based management.

    Steps:
    1. Expire only stale opps (older than STALE_HOURS)
    2. SQL: find multi-marketplace product clusters
    3. For each cluster: filter → calculate → score
    4. Upsert: update or insert, keeping active opps alive
    """
    rates = await get_rates()

    # Step 1: Expire opportunities whose buy/sell listings are no longer fresh
    await db.execute(text(f"""
        UPDATE opportunities o
        SET status = 'expired', expired_at = NOW()
        WHERE o.status = 'active'
          AND NOT EXISTS (
            SELECT 1 FROM product_listings pl
            WHERE pl.id IN (o.buy_listing_id, o.sell_listing_id)
              AND pl.scraped_at > NOW() - INTERVAL '{STALE_HOURS} hours'
          )
    """))
    await db.flush()

    # Step 2: Find products with FRESH listings in 2+ marketplaces (SQL-first)
    result = await db.execute(text(f"""
        SELECT pl.master_product_id, COUNT(DISTINCT pl.marketplace_id) AS mp_count
        FROM product_listings pl
        WHERE pl.scraped_at > NOW() - INTERVAL '{STALE_HOURS} hours'
        GROUP BY pl.master_product_id
        HAVING COUNT(DISTINCT pl.marketplace_id) >= 2
        ORDER BY COUNT(DISTINCT pl.id) DESC
    """))
    candidates = [(row[0], row[1]) for row in result.fetchall()]

    if not candidates:
        logger.info("No multi-marketplace candidates found")
        return []

    logger.info("Analyzing %d candidate products across %d+ marketplaces",
                len(candidates), min(r[1] for r in candidates))

    new_opps: list[Opportunity] = []

    # Process candidates in parallel batches.
    # Each coroutine gets its own AsyncSession to avoid corrupting shared
    # SQLAlchemy session state when multiple coroutines run concurrently.
    async def _analyze_with_own_session(product_id: int) -> Opportunity | None:
        async with async_session() as session:
            return await _analyze_product(session, product_id, rates)

    ANALYSIS_BATCH_SIZE = 20
    for i in range(0, len(candidates), ANALYSIS_BATCH_SIZE):
        batch = candidates[i:i + ANALYSIS_BATCH_SIZE]
        results = await asyncio.gather(
            *[_analyze_with_own_session(mp_id) for mp_id, _ in batch],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Opportunity):
                new_opps.append(r)
            elif isinstance(r, Exception):
                logger.error("Error analyzing product: %s", r)

    # Each _analyze_with_own_session commits independently, so we only
    # need to commit the expire/flush done on the caller's session.
    await db.commit()

    if new_opps:
        new_opps.sort(key=lambda o: o.opportunity_score, reverse=True)

        high = sum(1 for o in new_opps if o.confidence_level == "high")
        med = sum(1 for o in new_opps if o.confidence_level == "medium")
        low = sum(1 for o in new_opps if o.confidence_level == "low")
        logger.info("Detected %d opportunities (high=%d, med=%d, low=%d)",
                     len(new_opps), high, med, low)
    else:
        logger.info("No qualifying opportunities found")

    return new_opps


async def _analyze_product(
    db: AsyncSession, product_id: int, rates: dict[str, float]
) -> Opportunity | None:
    """Analyze a single product cluster for arbitrage. Returns best opportunity or None."""
    freshness_cutoff = datetime.now(timezone.utc) - timedelta(hours=STALE_HOURS)
    result = await db.execute(
        select(ProductListing)
        .where(
            ProductListing.master_product_id == product_id,
            ProductListing.scraped_at > freshness_cutoff,
        )
        .order_by(ProductListing.price)
    )
    all_listings = list(result.scalars().all())
    if len(all_listings) < 2:
        logger.debug("[scan] product %d: only %d listings, skip", product_id, len(all_listings))
        return None

    # Filter untrusted
    trusted = [l for l in all_listings if is_trustworthy(l)]
    if len(trusted) < 2:
        logger.debug("[scan] product %d: only %d trusted (of %d), skip", product_id, len(trusted), len(all_listings))
        return None

    # Remove outliers
    prices_usd = [to_usd(float(l.price), l.currency, rates) for l in trusted]
    trusted = [l for l, p in zip(trusted, prices_usd) if not is_outlier_price(p, prices_usd)]
    if len(trusted) < 2:
        logger.debug("[scan] product %d: only %d after outlier removal, skip", product_id, len(trusted))
        return None

    # Prefetch scoring data in 2 bulk queries (eliminates N+1)
    price_cache, age_cache = await _prefetch_scoring_data(db, trusted)

    # Group by marketplace
    by_mp: dict[str, list[ProductListing]] = {}
    for l in trusted:
        by_mp.setdefault(l.marketplace_id, []).append(l)

    if len(by_mp) < 2:
        logger.debug("[scan] product %d: only %d marketplaces after filtering, skip", product_id, len(by_mp))
        return None

    velocity = compute_velocity(trusted)
    mps = list(by_mp.keys())

    best_opp: Opportunity | None = None
    best_score = -1.0

    for buy_mp in mps:
        for sell_mp in mps:
            if buy_mp == sell_mp:
                continue
            # Only evaluate valid arbitrage routes
            if (buy_mp, sell_mp) not in VALID_ROUTE_PAIRS:
                logger.debug("[scan] product %d: route %s→%s not valid", product_id, buy_mp, sell_mp)
                continue

            buy_listings = by_mp[buy_mp]
            sell_listings = by_mp[sell_mp]

            # Cheapest buy
            buy_candidate = min(buy_listings, key=lambda l: to_usd(float(l.price), l.currency, rates))
            buy_usd = to_usd(float(buy_candidate.price), buy_candidate.currency, rates)

            # Compatible sells (same condition + variant + title match)
            compatible = [
                s for s in sell_listings
                if buy_candidate.condition == s.condition
                and variants_compatible(buy_candidate, s)
                and _titles_match(buy_candidate.title, s.title)
            ]
            if not compatible:
                # Log why no compatible sells
                for s in sell_listings:
                    reasons = []
                    if buy_candidate.condition != s.condition:
                        reasons.append(f"condition {buy_candidate.condition}!={s.condition}")
                    if not variants_compatible(buy_candidate, s):
                        reasons.append("variants_incompatible")
                    if not _titles_match(buy_candidate.title, s.title):
                        reasons.append("titles_no_match")
                    logger.debug("[scan] product %d: %s→%s sell rejected: %s", product_id, buy_mp, sell_mp, ", ".join(reasons))
                continue

            # Analyze competition ONLY among compatible listings.
            # Using unfiltered sell_listings inflates the realistic sell price
            # when the cluster contains mixed variants (e.g. 128GB + 256GB).
            comp = analyze_competition(compatible, rates)
            if comp.realistic_sell_usd <= 0:
                continue

            sell_candidate = max(compatible, key=lambda l: to_usd(float(l.price), l.currency, rates))
            sell_usd = comp.realistic_sell_usd

            if sell_usd <= buy_usd:
                continue

            # Full profit calc
            calc = calculate_profit(
                buy_usd, sell_usd, buy_mp, sell_mp,
                buy_candidate.is_free_shipping, sell_candidate.is_free_shipping,
            )

            # ── Hard filters (only reject clearly invalid data) ────
            if calc.net_profit < MIN_PROFIT_USD:
                continue
            if calc.margin < MIN_MARGIN:
                continue
            if calc.roi < MIN_ROI:
                continue

            price_ratio = sell_usd / buy_usd if buy_usd > 0 else float("inf")
            if price_ratio > MAX_PRICE_RATIO:
                logger.warning(
                    "REJECTED: price ratio %.1fx too high for product %d (%s->%s, buy=$%.2f sell=$%.2f)",
                    price_ratio, product_id, buy_mp, sell_mp, buy_usd, sell_usd,
                )
                continue
            if calc.roi > MAX_ROI:
                logger.warning(
                    "REJECTED: ROI %.1f%% too high for product %d (%s->%s)",
                    calc.roi * 100, product_id, buy_mp, sell_mp,
                )
                continue

            # ── Soft penalties (reduce score, don't reject) ──────
            soft_penalty = 0.0
            confidence_penalty = 0.0

            # Thin margin penalties
            if calc.margin < 0.05:
                soft_penalty += 15  # margin < 5%
            elif calc.margin < 0.08:
                soft_penalty += 10  # margin < 8%

            # High price ratio reduces confidence
            if price_ratio > 6.0:
                confidence_penalty += 15

            # Low seller ratings increase risk (handled in scoring via input)
            # but also apply a direct score penalty for very low ratings
            buy_rating = buy_candidate.seller_rating
            sell_rating = sell_candidate.seller_rating
            if (buy_rating is not None and buy_rating < 3.0) or \
               (sell_rating is not None and sell_rating < 3.0):
                soft_penalty += 5

            # High competition penalty
            if comp.competitor_count > 20:
                soft_penalty += 10

            # Scoring signals (from prefetched cache — zero queries)
            stability = _compute_stability_cached(
                price_cache.get(str(sell_candidate.url), [])
            )
            depth_score, daily_sales, monthly_sales, scalability = _compute_depth_cached(
                sell_listings, comp.competitor_count, age_cache,
            )
            listing_age = age_cache.get(str(sell_candidate.url), 1.0)

            # Compute price spread across sell listings
            sell_prices = [to_usd(float(l.price), l.currency, rates) for l in sell_listings]
            avg_sell = statistics.mean(sell_prices) if sell_prices else 1
            price_spread = ((max(sell_prices) - min(sell_prices)) / avg_sell) if len(sell_prices) > 1 and avg_sell > 0 else 0

            # Aggregate sales/review signals from sell-side listings
            total_sales = sum(l.sales_count or 0 for l in sell_listings)
            total_reviews = sum(l.reviews_count or 0 for l in sell_listings)

            scoring_input = ScoringInput(
                net_profit_usd=calc.net_profit,
                roi=calc.roi,
                margin=calc.margin,
                buy_price_usd=calc.buy_price_usd,
                competitor_count=comp.competitor_count,
                total_listings=len(all_listings),
                buy_seller_rating=buy_candidate.seller_rating,
                buy_seller_reviews=buy_candidate.reviews_count or 0,
                sell_seller_rating=sell_candidate.seller_rating,
                sell_seller_reviews=sell_candidate.reviews_count or 0,
                total_sales_count=total_sales,
                total_reviews_count=total_reviews,
                estimated_daily_sales=daily_sales,
                market_depth_score=depth_score,
                price_stability_score=stability,
                listing_age_days=listing_age,
                is_cross_border=is_cross_border(buy_mp, sell_mp),
                price_spread_pct=price_spread,
                route_difficulty=get_route_difficulty(buy_mp, sell_mp),
                soft_penalty=soft_penalty,
                confidence_penalty=confidence_penalty,
            )
            result = score_opportunity_v2(scoring_input)

            if result.opportunity_score > best_score:
                best_score = result.opportunity_score
                best_opp = Opportunity(
                    master_product_id=product_id,
                    buy_listing_id=buy_candidate.id,
                    sell_listing_id=sell_candidate.id,
                    buy_price=calc.buy_price_usd,
                    sell_price=calc.sell_price_usd,
                    estimated_sell_price=calc.sell_price_usd,
                    fees=calc.total_fees,
                    shipping_cost=round(calc.domestic_shipping + calc.international_shipping, 2),
                    net_profit=calc.net_profit,
                    roi=calc.roi,
                    buy_marketplace=buy_mp,
                    sell_marketplace=sell_mp,
                    route=get_route_label(buy_mp, sell_mp),
                    marketplace_fee=calc.marketplace_fee,
                    payment_fee=calc.payment_fee,
                    sell_tax=calc.sell_tax,
                    import_tax=calc.import_tax,
                    domestic_shipping=calc.domestic_shipping,
                    international_shipping=calc.international_shipping,
                    sales_velocity_score=velocity,
                    competition_score=comp.score,
                    price_stability_score=stability,
                    opportunity_score=result.opportunity_score,
                    confidence_level=result.confidence_level,
                    risk_score=result.risk_score,
                    confidence_score=result.confidence_score,
                    competitor_count=comp.competitor_count,
                    avg_market_price=comp.avg_price_usd,
                    lowest_competitor_price=comp.lowest_price_usd,
                    market_depth_score=depth_score,
                    estimated_daily_sales=daily_sales,
                    estimated_monthly_sales=monthly_sales,
                    scalability_level=scalability,
                    demand_trend_score=50.0,
                    demand_trend_label="stable",
                    capital_required=round(calc.buy_price_usd * max(1, monthly_sales), 2),
                    capital_efficiency_score=min(100, calc.roi * 200) if calc.roi > 0 else 0,
                    capital_tier="low" if calc.buy_price_usd < 50 else "medium" if calc.buy_price_usd < 200 else "high",
                    recommended_quantity=max(1, int(monthly_sales)),
                )

    if best_opp:
        # Upsert: check if active opp exists for this product+direction
        existing = await db.execute(
            select(Opportunity)
            .where(
                Opportunity.master_product_id == product_id,
                Opportunity.buy_marketplace == best_opp.buy_marketplace,
                Opportunity.sell_marketplace == best_opp.sell_marketplace,
                Opportunity.status == "active",
            )
        )
        old = existing.scalars().first()

        if old:
            # Update existing — keep the ID, refresh the data
            for attr in (
                "buy_listing_id", "sell_listing_id", "buy_price", "sell_price",
                "estimated_sell_price", "fees", "shipping_cost", "net_profit", "roi",
                "route", "marketplace_fee", "payment_fee", "sell_tax", "import_tax", "domestic_shipping",
                "international_shipping", "sales_velocity_score", "competition_score",
                "price_stability_score", "opportunity_score", "confidence_level",
                "risk_score", "confidence_score",
                "competitor_count", "avg_market_price", "lowest_competitor_price",
                "market_depth_score", "estimated_daily_sales", "estimated_monthly_sales",
                "scalability_level", "demand_trend_score", "demand_trend_label",
                "capital_required", "capital_efficiency_score", "capital_tier",
                "recommended_quantity",
            ):
                setattr(old, attr, getattr(best_opp, attr))
            await db.commit()
            logger.info(
                "UPDATED opp id=%d | %s->%s | profit $%.2f | margin %.0f%% | score %.0f",
                old.id, old.buy_marketplace, old.sell_marketplace,
                float(old.net_profit), calc.margin * 100, old.opportunity_score,
            )
            return old
        else:
            db.add(best_opp)
            await db.commit()
            logger.info(
                "NEW opp | %s->%s | profit $%.2f | margin %.0f%% | score %.0f",
                best_opp.buy_marketplace, best_opp.sell_marketplace,
                float(best_opp.net_profit), calc.margin * 100, best_opp.opportunity_score,
            )
            return best_opp

    return None
