"""
Advanced Arbitrage Detection Engine v2.

Key improvements over v1:
- SQL-first candidate filtering (push work to Postgres)
- Upsert-based opportunity management (no expire-all-then-redetect)
- Cached currency conversion with TTL
- Configurable thresholds via env
- Supports all conditions (new, refurbished, used)
- Percentage margin in output
- Batch processing for memory efficiency
- IQR outlier removal per product cluster

Algorithm:
1. Fetch exchange rates (cached 1h TTL)
2. SQL: find products with listings in 2+ marketplaces, pre-filter by condition
3. For each product cluster:
   a. Convert all prices to USD
   b. Remove statistical outliers (IQR method)
   c. Filter untrusted sellers (rating < 2.0)
   d. Check variant/condition compatibility
   e. For each (buy_mp, sell_mp) pair:
      - Buy price = cheapest on buy_mp
      - Sell price = median on sell_mp * 0.97 (undercut)
      - Calculate all fees, shipping, import tax
      - Net profit = sell - buy - all_costs
      - Margin = net_profit / sell_price
   f. Keep best opportunity per product (highest score)
4. Upsert: update existing active opps, create new ones, expire stale
5. Score 0-100 composite, rank by score
"""
import logging
import math
import os
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .currency import get_rates, to_usd
from .models import MasterProduct, Opportunity, PriceHistory, ProductListing
from .scoring import ScoringInput, score as score_opportunity_v2

logger = logging.getLogger(__name__)

# ── Configurable thresholds ───────────────────────────────
MIN_PROFIT_USD = float(os.getenv("MIN_PROFIT_USD", "20"))
MIN_MARGIN = float(os.getenv("MIN_MARGIN", "0.25"))       # 25%
MIN_ROI = float(os.getenv("MIN_ROI", "0.10"))             # 10%
MAX_ROI = float(os.getenv("MAX_ROI", "3.0"))              # 300% — anything above is likely bad data/scam
MAX_PRICE_RATIO = float(os.getenv("MAX_PRICE_RATIO", "5.0"))  # sell/buy > 5x = mismatched products or scam listing
STALE_HOURS = int(os.getenv("STALE_HOURS", "24"))         # expire opps older than this

# ── Fee tables ────────────────────────────────────────────
MARKETPLACE_FEES: dict[str, dict] = {
    "amazon": {
        "commission": 0.15,
        "payment_processing": 0.0,
        "domestic_shipping": 0.0,
        "currency": "MXN",
        "country": "MX",
    },
    "mercadolibre_mx": {
        "commission": 0.16,
        "payment_processing": 0.036,
        "domestic_shipping": 0.0,
        "currency": "MXN",
        "country": "MX",
    },
    "mercadolibre_ar": {
        "commission": 0.13,
        "payment_processing": 0.036,
        "domestic_shipping": 0.0,
        "currency": "ARS",
        "country": "AR",
    },
    "ebay": {
        "commission": 0.1312,
        "payment_processing": 0.0,
        "domestic_shipping": 8.0,
        "currency": "USD",
        "country": "US",
    },
}

CROSS_BORDER = {
    "import_tax_rate": 0.16,
    "international_shipping_usd": 25.0,
}


# ── Data structures ───────────────────────────────────────
@dataclass
class ProfitCalc:
    buy_price_usd: float
    sell_price_usd: float
    marketplace_fee: float
    payment_fee: float
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
def is_cross_border(buy_mp: str, sell_mp: str) -> bool:
    buy_country = MARKETPLACE_FEES.get(buy_mp, {}).get("country", "?")
    sell_country = MARKETPLACE_FEES.get(sell_mp, {}).get("country", "?")
    return buy_country != sell_country


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
    payment_fee = sell_usd * sell_fees.get("payment_processing", 0) + 0.30
    domestic_shipping = 0.0 if sell_free_ship else sell_fees.get("domestic_shipping", 5.0)

    international_shipping = 0.0
    import_tax = 0.0
    if is_cross_border(buy_mp, sell_mp):
        international_shipping = CROSS_BORDER["international_shipping_usd"]
        import_tax = buy_usd * CROSS_BORDER["import_tax_rate"]

    total_fees = marketplace_fee + payment_fee + import_tax + domestic_shipping + international_shipping
    net_profit = sell_usd - buy_usd - total_fees
    roi = net_profit / buy_usd if buy_usd > 0 else 0.0
    margin = net_profit / sell_usd if sell_usd > 0 else 0.0

    return ProfitCalc(
        buy_price_usd=round(buy_usd, 2),
        sell_price_usd=round(sell_usd, 2),
        marketplace_fee=round(marketplace_fee, 2),
        payment_fee=round(payment_fee, 2),
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
    """IQR-based outlier detection."""
    if len(prices) < 4:
        return False
    s = sorted(prices)
    q1, q3 = s[len(s) // 4], s[3 * len(s) // 4]
    iqr = q3 - q1
    return price < (q1 - 2.0 * iqr) or price > (q3 + 2.0 * iqr)


def is_trustworthy(listing: ProductListing) -> bool:
    if listing.seller_rating is not None and listing.seller_rating < 2.0:
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
    # If it has main product keywords, not an accessory
    for kw in MAIN_PRODUCT_KEYWORDS:
        if kw in title_lower:
            return False
    for kw in ACCESSORY_KEYWORDS:
        if kw in title_lower:
            return True
    return False


def _extract_model_numbers(title: str) -> set[str]:
    """Extract model identifiers like 'edge 540', 'wh-1000xm4', 'umc202hd'."""
    import re
    # Match patterns: letters+numbers (wh1000xm4), numbers after brand words (edge 540)
    patterns = re.findall(r'\b([a-z]+[\-]?\d{2,}[a-z0-9]*)\b', title)
    # Also match standalone model numbers like "540", "1040" after known product words
    model_words = re.findall(r'\b(?:edge|hero|mini|v|series|pro|wh|wf|xm)\s*(\d{2,}[a-z0-9]*)\b', title)
    return set(p.replace("-", "") for p in patterns) | set(model_words)


def variants_compatible(buy: ProductListing, sell: ProductListing) -> bool:
    """Prevent storage/bundle/accessory/model mismatches."""
    b, s = buy.title.lower(), sell.title.lower()

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

    # Storage mismatch
    b_storage = next((v for v in VARIANT_STORAGE if v in b), None)
    s_storage = next((v for v in VARIANT_STORAGE if v in s), None)
    if b_storage and s_storage and b_storage != s_storage:
        return False

    # Capacity mismatch (power banks, chargers)
    b_cap = next((v for v in VARIANT_CAPACITY if v in b.replace(" ", "")), None)
    s_cap = next((v for v in VARIANT_CAPACITY if v in s.replace(" ", "")), None)
    if b_cap and s_cap and b_cap != s_cap:
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

    # Step 1: Only expire truly stale opportunities
    await db.execute(text(f"""
        UPDATE opportunities
        SET status = 'expired', expired_at = NOW()
        WHERE status = 'active'
          AND created_at < NOW() - INTERVAL '{STALE_HOURS} hours'
    """))
    await db.flush()

    # Step 2: Find products with listings in 2+ marketplaces (SQL-first)
    result = await db.execute(text("""
        SELECT pl.master_product_id, COUNT(DISTINCT pl.marketplace_id) AS mp_count
        FROM product_listings pl
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

    for mp_id, _ in candidates:
        opp = await _analyze_product(db, mp_id, rates)
        if opp:
            new_opps.append(opp)

    if new_opps:
        await db.commit()
        new_opps.sort(key=lambda o: o.opportunity_score, reverse=True)

        high = sum(1 for o in new_opps if o.confidence_level == "high")
        med = sum(1 for o in new_opps if o.confidence_level == "medium")
        low = sum(1 for o in new_opps if o.confidence_level == "low")
        logger.info("Detected %d opportunities (high=%d, med=%d, low=%d)",
                     len(new_opps), high, med, low)
    else:
        await db.commit()
        logger.info("No qualifying opportunities found")

    return new_opps


async def _analyze_product(
    db: AsyncSession, product_id: int, rates: dict[str, float]
) -> Opportunity | None:
    """Analyze a single product cluster for arbitrage. Returns best opportunity or None."""
    # Load listings
    result = await db.execute(
        select(ProductListing)
        .where(ProductListing.master_product_id == product_id)
        .order_by(ProductListing.price)
    )
    all_listings = list(result.scalars().all())
    if len(all_listings) < 2:
        return None

    # Filter untrusted
    trusted = [l for l in all_listings if is_trustworthy(l)]
    if len(trusted) < 2:
        return None

    # Remove outliers
    prices_usd = [to_usd(float(l.price), l.currency, rates) for l in trusted]
    trusted = [l for l, p in zip(trusted, prices_usd) if not is_outlier_price(p, prices_usd)]
    if len(trusted) < 2:
        return None

    # Group by marketplace
    by_mp: dict[str, list[ProductListing]] = {}
    for l in trusted:
        by_mp.setdefault(l.marketplace_id, []).append(l)

    if len(by_mp) < 2:
        return None

    velocity = compute_velocity(trusted)
    mps = list(by_mp.keys())

    best_opp: Opportunity | None = None
    best_score = -1.0

    for i, buy_mp in enumerate(mps):
        for sell_mp in mps:
            if buy_mp == sell_mp:
                continue

            buy_listings = by_mp[buy_mp]
            sell_listings = by_mp[sell_mp]

            comp = analyze_competition(sell_listings, rates)
            if comp.realistic_sell_usd <= 0:
                continue

            # Cheapest buy
            buy_candidate = min(buy_listings, key=lambda l: to_usd(float(l.price), l.currency, rates))
            buy_usd = to_usd(float(buy_candidate.price), buy_candidate.currency, rates)

            # Compatible sells (same condition + variant)
            compatible = [
                s for s in sell_listings
                if buy_candidate.condition == s.condition
                and variants_compatible(buy_candidate, s)
            ]
            if not compatible:
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

            if calc.net_profit < MIN_PROFIT_USD:
                continue
            if calc.margin < MIN_MARGIN:
                continue
            if calc.roi < MIN_ROI:
                continue

            # Guard: reject absurd price ratios (likely mismatched products)
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

            # Scoring signals
            stability = await compute_stability(db, sell_candidate.url)
            depth_score, daily_sales, monthly_sales, scalability = await compute_depth(
                db, sell_listings, comp.competitor_count
            )
            listing_age = await _listing_age(db, sell_candidate.url)

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
                    marketplace_fee=calc.marketplace_fee,
                    payment_fee=calc.payment_fee,
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
                "marketplace_fee", "payment_fee", "import_tax", "domestic_shipping",
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
            logger.info(
                "UPDATED opp id=%d | %s->%s | profit $%.2f | margin %.0f%% | score %.0f",
                old.id, old.buy_marketplace, old.sell_marketplace,
                float(old.net_profit), calc.margin * 100, old.opportunity_score,
            )
            return old
        else:
            db.add(best_opp)
            logger.info(
                "NEW opp | %s->%s | profit $%.2f | margin %.0f%% | score %.0f",
                best_opp.buy_marketplace, best_opp.sell_marketplace,
                float(best_opp.net_profit), calc.margin * 100, best_opp.opportunity_score,
            )
            return best_opp

    return None
