"""
Production-grade Opportunity Engine.

Calculates real arbitrage profit considering:
- Per-marketplace commission rates
- Payment processing fees (3.6% + $0.30)
- Import taxes (cross-border)
- Domestic + international shipping
- Realistic sell price (median market, not max)
- Sales velocity scoring
- Competition analysis
- Price stability from historical data
- Condition/variant mismatch filtering
- Composite opportunity score 0-100
"""
import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import MasterProduct, Opportunity, OpportunityHistory, PriceHistory, ProductListing

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
# Fee tables per marketplace
# ──────────────────────────────────────────────────────────
MARKETPLACE_FEES: dict[str, dict] = {
    "amazon": {
        "commission": 0.15,        # 15% referral fee
        "payment_processing": 0.0, # included
        "domestic_shipping": 0.0,  # FBA or free usually
        "currency": "MXN",
    },
    "mercadolibre_mx": {
        "commission": 0.16,        # ~16% comision ML
        "payment_processing": 0.036, # Mercado Pago 3.6%
        "domestic_shipping": 0.0,    # free shipping subsidized
        "currency": "MXN",
    },
    "mercadolibre_ar": {
        "commission": 0.13,        # ~13% comision ML AR
        "payment_processing": 0.036,
        "domestic_shipping": 0.0,
        "currency": "ARS",
    },
    "ebay": {
        "commission": 0.1312,      # 13.12% final value fee
        "payment_processing": 0.0, # included in final value
        "domestic_shipping": 8.0,  # estimated USD
        "currency": "USD",
    },
}

# Cross-border costs
CROSS_BORDER = {
    "import_tax_rate": 0.16,       # IVA Mexico
    "international_shipping_usd": 25.0,  # estimated
}

# Thresholds for qualifying opportunities
MIN_PROFIT_USD = 40.0
MIN_ROI = 0.30
MIN_SALES_VELOCITY = 10.0    # minimum score to consider
MAX_COMPETITION_SCORE = 80.0 # above this = too saturated

# Scoring weights (must sum to 1.0)
W_PROFIT = 0.16
W_ROI = 0.11
W_VELOCITY = 0.15
W_COMPETITION = 0.10
W_STABILITY = 0.08
W_DEPTH = 0.13
W_DEMAND = 0.13
W_CAPITAL = 0.14

# Exchange rates cache
_exchange_rates: dict[str, float] = {}


async def fetch_exchange_rates() -> dict[str, float]:
    global _exchange_rates
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://open.er-api.com/v6/latest/USD")
            resp.raise_for_status()
            data = resp.json()
            _exchange_rates = data["rates"]
            _exchange_rates["USD"] = 1.0
            logger.info(
                "FX updated: ARS=%.2f MXN=%.2f EUR=%.2f",
                _exchange_rates.get("ARS", 0),
                _exchange_rates.get("MXN", 0),
                _exchange_rates.get("EUR", 0),
            )
    except Exception:
        logger.error("FX fetch failed, using fallback", exc_info=True)
        if not _exchange_rates:
            _exchange_rates = {"USD": 1.0, "ARS": 1450.0, "MXN": 17.8, "EUR": 0.86, "GBP": 0.75}
    return _exchange_rates


def to_usd(price: float, currency: str) -> float:
    if currency == "USD":
        return price
    rate = _exchange_rates.get(currency, 1.0)
    return price / rate if rate else price


def is_cross_border(buy_mp: str, sell_mp: str) -> bool:
    """Detects if the trade crosses country borders."""
    country_map = {
        "amazon": "MX", "mercadolibre_mx": "MX",
        "mercadolibre_ar": "AR",
        "ebay": "US",
    }
    return country_map.get(buy_mp, "?") != country_map.get(sell_mp, "?")


# ──────────────────────────────────────────────────────────
# Profit calculation
# ──────────────────────────────────────────────────────────
@dataclass
class TrueProfitCalc:
    buy_price_usd: float
    estimated_sell_price_usd: float
    marketplace_fee: float
    payment_fee: float
    import_tax: float
    domestic_shipping: float
    international_shipping: float
    total_fees: float
    net_profit: float
    roi: float


def calculate_true_profit(
    buy_price_usd: float,
    sell_price_usd: float,
    buy_marketplace: str,
    sell_marketplace: str,
    buy_shipping_free: bool = False,
    sell_shipping_free: bool = False,
) -> TrueProfitCalc:
    sell_fees = MARKETPLACE_FEES.get(sell_marketplace, MARKETPLACE_FEES["ebay"])

    # Marketplace commission on sell
    marketplace_fee = sell_price_usd * sell_fees["commission"]

    # Payment processing
    payment_fee = sell_price_usd * sell_fees.get("payment_processing", 0) + 0.30

    # Shipping
    domestic_shipping = 0.0 if sell_shipping_free else sell_fees.get("domestic_shipping", 5.0)

    # Cross-border costs
    international_shipping = 0.0
    import_tax = 0.0
    if is_cross_border(buy_marketplace, sell_marketplace):
        international_shipping = CROSS_BORDER["international_shipping_usd"]
        import_tax = buy_price_usd * CROSS_BORDER["import_tax_rate"]

    total_fees = marketplace_fee + payment_fee + import_tax + domestic_shipping + international_shipping
    net_profit = sell_price_usd - buy_price_usd - total_fees
    roi = net_profit / buy_price_usd if buy_price_usd > 0 else 0.0

    return TrueProfitCalc(
        buy_price_usd=round(buy_price_usd, 2),
        estimated_sell_price_usd=round(sell_price_usd, 2),
        marketplace_fee=round(marketplace_fee, 2),
        payment_fee=round(payment_fee, 2),
        import_tax=round(import_tax, 2),
        domestic_shipping=round(domestic_shipping, 2),
        international_shipping=round(international_shipping, 2),
        total_fees=round(total_fees, 2),
        net_profit=round(net_profit, 2),
        roi=round(roi, 4),
    )


# ──────────────────────────────────────────────────────────
# Sales velocity scoring
# ──────────────────────────────────────────────────────────
def compute_sales_velocity(listings: list[ProductListing]) -> float:
    """
    Score 0-100 based on market demand signals.
    High reviews + high sales + high rating = high velocity.
    """
    if not listings:
        return 0.0

    total_reviews = sum(l.reviews_count or 0 for l in listings)
    total_sales = sum(l.sales_count or 0 for l in listings)
    avg_rating = statistics.mean([l.seller_rating for l in listings if l.seller_rating]) if any(l.seller_rating for l in listings) else 0.0

    # Normalize each signal to 0-100
    review_score = min(100, (total_reviews / max(len(listings), 1)) * 2)  # 50 reviews/listing = 100
    sales_score = min(100, (total_sales / max(len(listings), 1)) * 1.5)   # 67 sales/listing = 100
    rating_score = (avg_rating / 5.0) * 100 if avg_rating > 0 else 30.0   # neutral if unknown

    return round(review_score * 0.4 + sales_score * 0.4 + rating_score * 0.2, 1)


# ──────────────────────────────────────────────────────────
# Competition analysis
# ──────────────────────────────────────────────────────────
@dataclass
class CompetitionInfo:
    score: float           # 0 = no competition, 100 = ultra saturated
    competitor_count: int
    lowest_price_usd: float
    avg_price_usd: float
    realistic_sell_price_usd: float  # median-based estimate


def analyze_competition(
    sell_marketplace_listings: list[ProductListing],
) -> CompetitionInfo:
    """
    Instead of selling at the highest price, we estimate a realistic price:
    the MEDIAN of all listings on the sell marketplace for the same product.
    """
    if not sell_marketplace_listings:
        return CompetitionInfo(0, 0, 0, 0, 0)

    prices_usd = sorted(
        to_usd(float(l.price), l.currency) for l in sell_marketplace_listings
    )
    count = len(prices_usd)
    lowest = prices_usd[0]
    avg = statistics.mean(prices_usd)
    median = statistics.median(prices_usd)

    # Realistic sell price = slightly below median (undercut strategy)
    realistic = median * 0.97  # 3% undercut

    # Competition score: more sellers = more competition
    if count <= 1:
        score = 10.0
    elif count <= 3:
        score = 30.0
    elif count <= 7:
        score = 55.0
    elif count <= 15:
        score = 75.0
    else:
        score = 90.0

    # Tighten score if price spread is small (everyone priced similar = hard to differentiate)
    if count > 1:
        spread = (prices_usd[-1] - prices_usd[0]) / avg if avg > 0 else 0
        if spread < 0.05:  # less than 5% price range
            score = min(100, score + 10)

    return CompetitionInfo(
        score=round(score, 1),
        competitor_count=count,
        lowest_price_usd=round(lowest, 2),
        avg_price_usd=round(avg, 2),
        realistic_sell_price_usd=round(realistic, 2),
    )


# ──────────────────────────────────────────────────────────
# Price stability from history
# ──────────────────────────────────────────────────────────
async def compute_price_stability(
    db: AsyncSession,
    listing_url: str,
) -> float:
    """
    Score 0-100. High = stable prices (good for arbitrage).
    Low = volatile prices (risky).
    """
    result = await db.execute(
        select(PriceHistory.price)
        .where(PriceHistory.listing_url == listing_url)
        .order_by(PriceHistory.recorded_at.desc())
        .limit(20)
    )
    prices = [float(row[0]) for row in result.all()]

    if len(prices) < 2:
        return 50.0  # neutral if not enough data

    avg = statistics.mean(prices)
    if avg == 0:
        return 50.0

    # Coefficient of variation: stddev / mean
    cv = statistics.stdev(prices) / avg

    # Low CV = stable. CV of 0 = perfect stability (100), CV > 0.3 = volatile (0)
    stability = max(0, min(100, (1 - cv / 0.3) * 100))
    return round(stability, 1)


# ──────────────────────────────────────────────────────────
# Market depth estimation
# ──────────────────────────────────────────────────────────
@dataclass
class MarketDepthInfo:
    score: float                # 0-100
    estimated_daily_sales: float
    estimated_monthly_sales: float
    scalability_level: str      # low / medium / high


async def compute_market_depth(
    db: AsyncSession,
    sell_listings: list[ProductListing],
    competitor_count: int,
) -> MarketDepthInfo:
    """
    Estimate how many units sell per day using available heuristics:

    1. Sales velocity from sales_count / listing age (days since first price record)
    2. Review accumulation rate as a proxy for purchase rate
    3. Competitor density: more sellers = larger market
    4. Stock turnover signal: low stock + high sales = fast-moving
    """
    if not sell_listings:
        return MarketDepthInfo(0, 0, 0, "low")

    daily_estimates: list[float] = []

    for listing in sell_listings:
        # --- Heuristic 1: sales_count / listing_age ---
        listing_age_days = await _get_listing_age_days(db, listing.url)
        if listing.sales_count and listing.sales_count > 0 and listing_age_days > 0:
            daily_from_sales = listing.sales_count / listing_age_days
            daily_estimates.append(daily_from_sales)

        # --- Heuristic 2: reviews as purchase proxy ---
        # Industry rule of thumb: ~1-5% of buyers leave a review.
        # We use 3% as middle estimate -> purchases = reviews / 0.03
        if listing.reviews_count and listing.reviews_count > 0 and listing_age_days > 0:
            estimated_purchases = listing.reviews_count / 0.03
            daily_from_reviews = estimated_purchases / max(listing_age_days, 1)
            daily_estimates.append(daily_from_reviews)

        # --- Heuristic 3: stock turnover signal ---
        # If stock is reported low (<5) but sales_count is high, product moves fast
        if (listing.stock_available is not None
                and listing.stock_available > 0
                and listing.sales_count
                and listing.sales_count > 0):
            # turnover_ratio: high sales relative to current stock = fast mover
            turnover_ratio = listing.sales_count / listing.stock_available
            # Convert to daily estimate: assume stock replenishes every ~7 days
            daily_from_stock = turnover_ratio / 7.0
            daily_estimates.append(daily_from_stock)

    # --- Heuristic 4: competitor density boost ---
    # More competitors = larger addressable market
    competitor_multiplier = 1.0
    if competitor_count >= 10:
        competitor_multiplier = 1.5
    elif competitor_count >= 5:
        competitor_multiplier = 1.2

    # Aggregate: use median of all estimates to reduce outlier impact
    if daily_estimates:
        raw_daily = statistics.median(daily_estimates) * competitor_multiplier
    else:
        # Fallback: use competitor count as a weak signal (1 sale/day per 3 sellers)
        raw_daily = max(0.1, competitor_count / 3.0)

    estimated_daily = round(raw_daily, 2)
    estimated_monthly = round(raw_daily * 30, 1)

    # Score 0-100 via log curve: ~20 at 0.1/day, ~45 at 2/day, ~70 at 10/day, ~95 at 50/day
    if estimated_daily <= 0:
        score = 0.0
    else:
        score = min(100, 20 + 25 * math.log2(estimated_daily + 1))

    score = round(score, 1)

    if score >= 70:
        scalability = "high"
    elif score >= 40:
        scalability = "medium"
    else:
        scalability = "low"

    return MarketDepthInfo(
        score=score,
        estimated_daily_sales=estimated_daily,
        estimated_monthly_sales=estimated_monthly,
        scalability_level=scalability,
    )


async def _get_listing_age_days(db: AsyncSession, listing_url: str) -> float:
    """How many days ago was this listing first seen in price_history."""
    result = await db.execute(
        select(func.min(PriceHistory.recorded_at))
        .where(PriceHistory.listing_url == listing_url)
    )
    first_seen = result.scalar()
    if not first_seen:
        return 0.0
    now = datetime.now(timezone.utc)
    delta = (now - first_seen).total_seconds() / 86400.0
    return max(delta, 1.0)  # at least 1 day to avoid division by zero


# ──────────────────────────────────────────────────────────
# Demand trend detection
# ──────────────────────────────────────────────────────────
@dataclass
class DemandTrendInfo:
    score: float          # 0-100: 0=collapsing, 50=stable, 100=surging
    label: str            # "rising" / "stable" / "declining"


async def compute_demand_trend(
    db: AsyncSession,
    listings: list[ProductListing],
) -> DemandTrendInfo:
    """
    Detect demand direction by combining 4 independent indicators:

    1. Price trajectory  - rising prices signal rising demand
    2. Review growth     - accelerating reviews = more buyers
    3. Stock depletion   - low/dropping stock = demand outpacing supply
    4. Price volatility  - high volatility during uptrend = demand shock

    Each indicator produces a sub-score in [-1, +1]:
      -1 = strongly declining, 0 = stable, +1 = strongly rising.
    The composite is mapped to 0-100 and classified.
    """
    signals: list[float] = []
    weights: list[float] = []

    # --- Indicator 1: Price trajectory (linear slope over recent history) ---
    price_slope = await _price_trend_slope(db, listings)
    if price_slope is not None:
        signals.append(price_slope)
        weights.append(0.35)

    # --- Indicator 2: Review growth rate ---
    review_signal = _review_growth_signal(listings)
    if review_signal is not None:
        signals.append(review_signal)
        weights.append(0.25)

    # --- Indicator 3: Stock depletion ---
    stock_signal = _stock_depletion_signal(listings)
    if stock_signal is not None:
        signals.append(stock_signal)
        weights.append(0.20)

    # --- Indicator 4: Price volatility direction ---
    vol_signal = await _volatility_direction_signal(db, listings)
    if vol_signal is not None:
        signals.append(vol_signal)
        weights.append(0.20)

    if not signals:
        return DemandTrendInfo(score=50.0, label="stable")

    # Weighted average of signals (each in [-1, +1])
    total_weight = sum(weights)
    composite = sum(s * w for s, w in zip(signals, weights)) / total_weight

    # Map [-1, +1] -> [0, 100]
    score = round(max(0, min(100, (composite + 1) * 50)), 1)

    if score >= 65:
        label = "rising"
    elif score <= 35:
        label = "declining"
    else:
        label = "stable"

    return DemandTrendInfo(score=score, label=label)


async def _price_trend_slope(
    db: AsyncSession,
    listings: list[ProductListing],
) -> float | None:
    """
    Compute average price slope across all listings using linear regression
    on recent price_history. Returns value in [-1, +1].

    Positive slope = prices rising = demand rising.
    """
    slopes: list[float] = []

    for listing in listings[:5]:  # cap to avoid too many queries
        result = await db.execute(
            select(PriceHistory.price, PriceHistory.recorded_at)
            .where(PriceHistory.listing_url == listing.url)
            .order_by(PriceHistory.recorded_at.asc())
            .limit(30)
        )
        rows = result.all()
        if len(rows) < 3:
            continue

        prices = [float(r[0]) for r in rows]
        avg_price = statistics.mean(prices)
        if avg_price == 0:
            continue

        # Simple linear regression: slope of price over time indices
        n = len(prices)
        x_mean = (n - 1) / 2.0
        y_mean = avg_price
        numerator = sum((i - x_mean) * (p - y_mean) for i, p in enumerate(prices))
        denominator = sum((i - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            continue

        slope_per_step = numerator / denominator
        # Normalize: slope as fraction of average price, clamped to [-1, +1]
        relative_slope = slope_per_step / avg_price
        # Scale so that ~5% total change over the window = +-1.0
        scaled = max(-1.0, min(1.0, relative_slope * n / 0.05))
        slopes.append(scaled)

    if not slopes:
        return None
    return statistics.mean(slopes)


def _review_growth_signal(listings: list[ProductListing]) -> float | None:
    """
    Higher reviews_count relative to listing age suggests sustained buyer interest.
    Compare review density across listings: listings with >20 reviews/month
    indicate strong demand.

    Returns [-1, +1]. We can only measure absolute level without historical
    review snapshots, so we use review density as a proxy.
    """
    densities: list[float] = []
    for listing in listings:
        reviews = listing.reviews_count or 0
        if reviews == 0:
            continue
        # Use sales_count as a cross-reference: high reviews + high sales = strong signal
        sales = listing.sales_count or 0
        # Review-to-sales ratio: healthy marketplace typically 1-10%
        # High ratio means many buyers are engaged enough to review
        if sales > 0:
            review_ratio = reviews / sales
            # >5% review rate = highly engaged buyers = strong demand
            densities.append(min(1.0, max(-1.0, (review_ratio - 0.03) / 0.07)))
        else:
            # No sales data, just use review count as absolute signal
            # 50+ reviews = strong signal
            densities.append(min(1.0, (reviews - 10) / 40))

    if not densities:
        return None
    return max(-1.0, min(1.0, statistics.mean(densities)))


def _stock_depletion_signal(listings: list[ProductListing]) -> float | None:
    """
    Low stock with high sales = demand outpacing supply = rising demand.
    High stock with low sales = oversupply = declining demand.

    Returns [-1, +1].
    """
    signals: list[float] = []
    for listing in listings:
        stock = listing.stock_available
        sales = listing.sales_count or 0

        if stock is None or stock <= 0:
            continue

        if sales == 0 and stock > 20:
            # High stock, no sales = weak demand
            signals.append(-0.5)
            continue

        # stock_pressure: sales / stock ratio
        # >5 = very high pressure (demand >> supply)
        # <0.5 = low pressure (supply >> demand)
        pressure = sales / stock
        normalized = max(-1.0, min(1.0, (math.log2(pressure + 0.1) + 1) / 3))
        signals.append(normalized)

    if not signals:
        return None
    return statistics.mean(signals)


async def _volatility_direction_signal(
    db: AsyncSession,
    listings: list[ProductListing],
) -> float | None:
    """
    Compare volatility and direction of recent vs older prices.
    Rising volatility + rising prices = demand surge.
    Rising volatility + falling prices = panic/clearance.
    Stable low volatility = stable demand.

    Returns [-1, +1].
    """
    signals: list[float] = []

    for listing in listings[:5]:
        result = await db.execute(
            select(PriceHistory.price)
            .where(PriceHistory.listing_url == listing.url)
            .order_by(PriceHistory.recorded_at.desc())
            .limit(20)
        )
        prices = [float(r[0]) for r in result.all()]
        if len(prices) < 6:
            continue

        # Split into recent half and older half
        mid = len(prices) // 2
        recent = prices[:mid]    # newest first (desc order)
        older = prices[mid:]

        recent_avg = statistics.mean(recent)
        older_avg = statistics.mean(older)

        if older_avg == 0:
            continue

        # Price direction: positive = rising
        price_direction = (recent_avg - older_avg) / older_avg

        # Volatility change
        recent_cv = (statistics.stdev(recent) / recent_avg) if recent_avg > 0 and len(recent) > 1 else 0
        older_cv = (statistics.stdev(older) / older_avg) if older_avg > 0 and len(older) > 1 else 0

        vol_change = recent_cv - older_cv

        # Combine: direction weighted by volatility context
        # Rising prices + rising volatility = strong demand signal
        # Falling prices + rising volatility = clearance (negative)
        if price_direction > 0:
            signal = min(1.0, price_direction * 10 + vol_change * 5)
        else:
            signal = max(-1.0, price_direction * 10 - abs(vol_change) * 5)

        signals.append(max(-1.0, min(1.0, signal)))

    if not signals:
        return None
    return statistics.mean(signals)


# ──────────────────────────────────────────────────────────
# Fake / outlier detection
# ──────────────────────────────────────────────────────────
def is_outlier_price(price_usd: float, all_prices_usd: list[float]) -> bool:
    """Detect if a price is a statistical outlier using IQR method."""
    if len(all_prices_usd) < 4:
        return False
    sorted_prices = sorted(all_prices_usd)
    q1 = sorted_prices[len(sorted_prices) // 4]
    q3 = sorted_prices[3 * len(sorted_prices) // 4]
    iqr = q3 - q1
    lower = q1 - 2.0 * iqr
    upper = q3 + 2.0 * iqr
    return price_usd < lower or price_usd > upper


def is_trustworthy_listing(listing: ProductListing) -> bool:
    """Filter out listings that are likely fake or unreliable."""
    # Very low seller rating
    if listing.seller_rating is not None and listing.seller_rating < 2.0:
        return False
    # Condition must match (we only compare same condition)
    return True


# ──────────────────────────────────────────────────────────
# Variant / condition mismatch detection
# ──────────────────────────────────────────────────────────
VARIANT_KEYWORDS = {
    "storage": [
        ("64gb", "128gb", "256gb", "512gb", "1tb", "2tb"),
    ],
    "color": [
        ("black", "white", "blue", "red", "silver", "gold", "green", "purple"),
    ],
    "bundle": [
        ("bundle", "combo", "kit", "pack", "set", "con funda", "con case"),
    ],
}


def extract_variants(title: str) -> dict[str, str | None]:
    """Extract storage, color, bundle info from title."""
    lower = title.lower()
    variants: dict[str, str | None] = {}

    # Storage
    for size in VARIANT_KEYWORDS["storage"][0]:
        if size in lower:
            variants["storage"] = size
            break
    else:
        variants["storage"] = None

    # Bundle detection
    variants["is_bundle"] = None
    for kw in VARIANT_KEYWORDS["bundle"][0]:
        if kw in lower:
            variants["is_bundle"] = "yes"
            break

    return variants


def conditions_compatible(buy: ProductListing, sell: ProductListing) -> bool:
    """Only compare same condition: new vs new, used vs used."""
    return buy.condition == sell.condition


def variants_compatible(buy: ProductListing, sell: ProductListing) -> bool:
    """Don't match 64GB vs 256GB, or single item vs bundle."""
    buy_v = extract_variants(buy.title)
    sell_v = extract_variants(sell.title)

    # Storage mismatch
    if buy_v["storage"] and sell_v["storage"] and buy_v["storage"] != sell_v["storage"]:
        return False

    # Bundle mismatch: can't buy single and claim bundle price
    if buy_v["is_bundle"] != sell_v["is_bundle"]:
        return False

    return True


# ──────────────────────────────────────────────────────────
# Capital efficiency: profit per dollar invested
# ──────────────────────────────────────────────────────────
class CapitalEfficiencyInfo:
    __slots__ = ("capital_required", "score", "tier", "recommended_quantity")

    def __init__(
        self,
        capital_required: float,
        score: float,
        tier: str,
        recommended_quantity: int,
    ):
        self.capital_required = capital_required
        self.score = score
        self.tier = tier
        self.recommended_quantity = recommended_quantity


def compute_capital_efficiency(
    buy_price: float,
    net_profit: float,
    roi: float,
    estimated_monthly_sales: float,
    sales_velocity_score: float,
) -> CapitalEfficiencyInfo:
    """
    Score how efficiently capital is deployed.

    High score = high profit relative to capital invested.
    Considers:
    - capital_required = buy_price * estimated_monthly_sales (inventory cost)
    - profit_per_dollar = monthly_profit / capital_required
    - roi and velocity as multipliers
    """
    monthly_qty = max(1, estimated_monthly_sales)
    capital_required = round(buy_price * monthly_qty, 2)

    # Monthly profit potential
    monthly_profit = net_profit * monthly_qty

    # Profit per dollar of capital invested
    if capital_required > 0:
        profit_per_dollar = monthly_profit / capital_required
    else:
        profit_per_dollar = 0.0

    # Normalize profit_per_dollar to 0-100
    # $0.50 profit per dollar invested = 100 (50% monthly return is excellent)
    ppd_norm = min(100, (profit_per_dollar / 0.50) * 100)

    # ROI component (already 0-1+, normalize to 0-100)
    roi_norm = min(100, roi * 100) if roi > 0 else 0

    # Velocity boost: fast-selling = capital turns over quickly
    velocity_norm = min(100, sales_velocity_score)

    # Capital accessibility: lower capital = more accessible
    # $0 = 100, $500 = 50, $2000 = 20, $5000+ = ~5
    if capital_required <= 0:
        access_norm = 100.0
    else:
        access_norm = min(100, max(5, 100 / (1 + capital_required / 500)))

    # Weighted score
    score = (
        ppd_norm * 0.35
        + roi_norm * 0.25
        + velocity_norm * 0.20
        + access_norm * 0.20
    )
    score = round(min(100, max(0, score)), 1)

    # Capital tier
    if capital_required <= 200:
        tier = "low"
    elif capital_required <= 1000:
        tier = "medium"
    else:
        tier = "high"

    # Recommended quantity: balance profit potential vs capital risk
    # Start with monthly estimate, cap based on capital tier
    if tier == "low":
        max_qty = int(monthly_qty)
    elif tier == "medium":
        max_qty = min(int(monthly_qty), max(1, int(500 / buy_price))) if buy_price > 0 else int(monthly_qty)
    else:
        max_qty = min(int(monthly_qty), max(1, int(1000 / buy_price))) if buy_price > 0 else int(monthly_qty)

    recommended_quantity = max(1, max_qty)

    return CapitalEfficiencyInfo(
        capital_required=capital_required,
        score=score,
        tier=tier,
        recommended_quantity=recommended_quantity,
    )


# ──────────────────────────────────────────────────────────
# Opportunity scoring: composite 0-100
# ──────────────────────────────────────────────────────────
def compute_opportunity_score(
    roi: float,
    net_profit: float,
    velocity: float,
    competition: float,
    stability: float,
    depth: float = 0.0,
    demand_trend: float = 50.0,
    capital_efficiency: float = 0.0,
) -> tuple[float, str]:
    """
    Returns (score, confidence_level).
    Weighted composite of all signals including market depth, demand trend,
    and capital efficiency.
    """
    # Normalize profit to 0-100 (>$200 profit = 100)
    profit_norm = min(100, (net_profit / 200) * 100) if net_profit > 0 else 0

    # Normalize ROI to 0-100 (>100% ROI = 100)
    roi_norm = min(100, roi * 100) if roi > 0 else 0

    # Competition is inverse: low competition = high score
    comp_norm = max(0, 100 - competition)

    # Demand trend: already 0-100 where >50 = rising (good for arbitrage)
    demand_norm = demand_trend

    score = (
        profit_norm * W_PROFIT
        + roi_norm * W_ROI
        + velocity * W_VELOCITY
        + comp_norm * W_COMPETITION
        + stability * W_STABILITY
        + depth * W_DEPTH
        + demand_norm * W_DEMAND
        + capital_efficiency * W_CAPITAL
    )

    score = round(min(100, max(0, score)), 1)

    if score >= 75:
        confidence = "high"
    elif score >= 50:
        confidence = "medium"
    else:
        confidence = "low"

    return score, confidence


# ──────────────────────────────────────────────────────────
# Main detection pipeline
# ──────────────────────────────────────────────────────────
async def detect_opportunities(db: AsyncSession) -> list[Opportunity]:
    """
    Production-grade arbitrage detection:
    1. Fetch exchange rates
    2. Find products with listings in 2+ marketplaces
    3. Filter: condition, variants, outliers, trust
    4. Calculate realistic sell price (median)
    5. Calculate true profit with all fees
    6. Score and rank
    7. Deduplicate: one best opportunity per product
    8. Apply quality thresholds
    """
    await fetch_exchange_rates()

    # Expire old active opportunities
    await db.execute(
        text("""
            UPDATE opportunities SET status = 'expired', expired_at = now()
            WHERE status = 'active'
        """)
    )
    await db.flush()

    # Find products with multi-marketplace listings
    result = await db.execute(
        text("""
            SELECT master_product_id
            FROM product_listings
            WHERE condition = 'new'
            GROUP BY master_product_id
            HAVING COUNT(DISTINCT marketplace_id) >= 2
        """)
    )
    candidate_ids = [row[0] for row in result.fetchall()]

    if not candidate_ids:
        logger.info("No multi-marketplace candidates found")
        return []

    logger.info("Analyzing %d candidate products", len(candidate_ids))
    new_opportunities: list[Opportunity] = []

    for mp_id in candidate_ids:
        listings_result = await db.execute(
            select(ProductListing)
            .where(
                ProductListing.master_product_id == mp_id,
                ProductListing.condition == "new",
            )
            .order_by(ProductListing.price)
        )
        all_listings = list(listings_result.scalars().all())

        if len(all_listings) < 2:
            continue

        # Filter untrusted listings
        trusted = [l for l in all_listings if is_trustworthy_listing(l)]
        if len(trusted) < 2:
            continue

        # Compute all USD prices for outlier detection
        all_prices_usd = [to_usd(float(l.price), l.currency) for l in trusted]

        # Remove price outliers
        trusted = [
            l for l, p in zip(trusted, all_prices_usd)
            if not is_outlier_price(p, all_prices_usd)
        ]
        if len(trusted) < 2:
            continue

        # Sales velocity for this product
        velocity = compute_sales_velocity(trusted)

        # Group by marketplace
        by_mp: dict[str, list[ProductListing]] = {}
        for l in trusted:
            by_mp.setdefault(l.marketplace_id, []).append(l)

        if len(by_mp) < 2:
            continue

        # For each marketplace pair, find best opportunity
        marketplaces = list(by_mp.keys())
        best_for_product: Opportunity | None = None
        best_score = -1.0

        for i, buy_mp in enumerate(marketplaces):
            for sell_mp in marketplaces[i + 1:]:
                # Try both directions
                for b_mp, s_mp in [(buy_mp, sell_mp), (sell_mp, buy_mp)]:
                    buy_listings = by_mp[b_mp]
                    sell_listings = by_mp[s_mp]

                    # Competition analysis on sell side
                    comp = analyze_competition(sell_listings)
                    if comp.realistic_sell_price_usd <= 0:
                        continue

                    # Best buy = cheapest on buy marketplace
                    buy_candidate = min(
                        buy_listings,
                        key=lambda l: to_usd(float(l.price), l.currency),
                    )
                    buy_usd = to_usd(float(buy_candidate.price), buy_candidate.currency)

                    # Find best compatible sell listing
                    compatible_sells = [
                        s for s in sell_listings
                        if conditions_compatible(buy_candidate, s)
                        and variants_compatible(buy_candidate, s)
                    ]
                    if not compatible_sells:
                        continue

                    sell_candidate = max(
                        compatible_sells,
                        key=lambda l: to_usd(float(l.price), l.currency),
                    )

                    # Use realistic (median) sell price, not max
                    realistic_sell_usd = comp.realistic_sell_price_usd

                    if realistic_sell_usd <= buy_usd:
                        continue

                    # True profit with all costs
                    calc = calculate_true_profit(
                        buy_usd, realistic_sell_usd,
                        b_mp, s_mp,
                        buy_candidate.is_free_shipping,
                        sell_candidate.is_free_shipping,
                    )

                    if calc.net_profit < MIN_PROFIT_USD:
                        continue
                    if calc.roi < MIN_ROI:
                        continue

                    # Price stability
                    stability = await compute_price_stability(db, sell_candidate.url)

                    # Market depth
                    depth_info = await compute_market_depth(
                        db, sell_listings, comp.competitor_count,
                    )

                    # Demand trend
                    trend_info = await compute_demand_trend(db, sell_listings)

                    # Capital efficiency
                    cap_info = compute_capital_efficiency(
                        buy_usd, calc.net_profit, calc.roi,
                        depth_info.estimated_monthly_sales,
                        velocity,
                    )

                    # Opportunity score
                    score, confidence = compute_opportunity_score(
                        calc.roi, calc.net_profit,
                        velocity, comp.score, stability,
                        depth_info.score, trend_info.score,
                        cap_info.score,
                    )

                    if score > best_score:
                        best_score = score
                        total_fees = calc.marketplace_fee + calc.payment_fee + calc.import_tax + calc.domestic_shipping + calc.international_shipping
                        best_for_product = Opportunity(
                            master_product_id=mp_id,
                            buy_listing_id=buy_candidate.id,
                            sell_listing_id=sell_candidate.id,
                            buy_price=round(buy_usd, 2),
                            sell_price=round(realistic_sell_usd, 2),
                            fees=round(total_fees, 2),
                            shipping_cost=round(calc.domestic_shipping + calc.international_shipping, 2),
                            net_profit=calc.net_profit,
                            roi=calc.roi,
                            buy_marketplace=b_mp,
                            sell_marketplace=s_mp,
                            estimated_sell_price=round(realistic_sell_usd, 2),
                            marketplace_fee=calc.marketplace_fee,
                            payment_fee=calc.payment_fee,
                            import_tax=calc.import_tax,
                            domestic_shipping=calc.domestic_shipping,
                            international_shipping=calc.international_shipping,
                            sales_velocity_score=velocity,
                            competition_score=comp.score,
                            price_stability_score=stability,
                            opportunity_score=score,
                            confidence_level=confidence,
                            competitor_count=comp.competitor_count,
                            avg_market_price=round(comp.avg_price_usd, 2),
                            lowest_competitor_price=round(comp.lowest_price_usd, 2),
                            market_depth_score=depth_info.score,
                            estimated_daily_sales=depth_info.estimated_daily_sales,
                            estimated_monthly_sales=depth_info.estimated_monthly_sales,
                            scalability_level=depth_info.scalability_level,
                            demand_trend_score=trend_info.score,
                            demand_trend_label=trend_info.label,
                            capital_required=cap_info.capital_required,
                            capital_efficiency_score=cap_info.score,
                            capital_tier=cap_info.tier,
                            recommended_quantity=cap_info.recommended_quantity,
                        )

        if best_for_product:
            db.add(best_for_product)
            new_opportunities.append(best_for_product)
            logger.info(
                "OPP [%s] %s -> %s | profit $%.2f | ROI %.0f%% | score %.0f (%s)",
                best_for_product.confidence_level.upper(),
                best_for_product.buy_marketplace,
                best_for_product.sell_marketplace,
                float(best_for_product.net_profit),
                best_for_product.roi * 100,
                best_for_product.opportunity_score,
                mp_id,
            )

    if new_opportunities:
        await db.commit()
        # Sort by score descending
        new_opportunities.sort(key=lambda o: o.opportunity_score, reverse=True)
        logger.info(
            "Detected %d opportunities (high: %d, med: %d, low: %d)",
            len(new_opportunities),
            sum(1 for o in new_opportunities if o.confidence_level == "high"),
            sum(1 for o in new_opportunities if o.confidence_level == "medium"),
            sum(1 for o in new_opportunities if o.confidence_level == "low"),
        )
    else:
        await db.commit()
        logger.info("No qualifying opportunities found")

    return new_opportunities
