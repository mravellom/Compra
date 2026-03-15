"""
Opportunity Scoring System v3.

Produces three scores per opportunity:
  - opportunity_score (0-100): overall quality ranking
  - risk_score (0-100): downside/failure probability (lower = safer)
  - confidence_score (0-100): data reliability (higher = more trustworthy)

Seven input factors:
  1. price_margin       - net profit margin after all fees
  2. marketplace_demand - estimated sales velocity and depth
  3. historical_sales   - observed sales frequency and review velocity
  4. price_volatility   - price stability over time (from price_history)
  5. competition        - number of competing listings (liquidity proxy)
  6. seller_reputation  - buy/sell side seller quality
  7. price_stability    - CV-based price consistency

v3 changes:
  - Rebalanced weights: demand + sales = 45% (reseller focus)
  - Soft penalty system: thin margins, high ratios, low trust reduce score
  - Cross-border risk scaled down: domestic=10, easy=30, medium=50, hard=70
  - Margin risk tiers adjusted for 3-8% realistic arbitrage margins

─────────────────────────────────────────────────────────────────
FORMULAS

  opportunity_score = Σ(normalized_factor_i × weight_i)
                    - soft_penalty

  risk_score = w1 × volatility_risk + w2 × competition_risk
             + w3 × liquidity_risk  + w4 × margin_risk
             + w5 × cross_border_risk

  confidence_score = data_coverage × signal_strength × consistency
                   - confidence_penalty

─────────────────────────────────────────────────────────────────
"""
import math
import statistics
from dataclasses import dataclass, field

# ── Factor normalization (all produce 0-100) ────────────────


def _normalize_margin(margin: float) -> float:
    """Map margin (0.0 - 1.0) to 0-100. 50% margin = 100."""
    return min(100, max(0, margin * 200))


def _normalize_roi(roi: float) -> float:
    """Map ROI to 0-100 using diminishing returns curve.
    ROI 0.50 (50%) → ~76, ROI 1.0 (100%) → ~92, ROI 2.0 → ~98."""
    if roi <= 0:
        return 0
    return min(100, 100 * (1 - math.exp(-2.5 * roi)))


def _normalize_profit(profit_usd: float, ceiling: float = 100.0) -> float:
    """Map USD profit to 0-100 using diminishing returns.
    $50 → ~63, $100 → ~92, $200 → ~99. Realistic for marketplace arbitrage."""
    if profit_usd <= 0:
        return 0
    return min(100, 100 * (1 - math.exp(-3.0 * profit_usd / ceiling)))


def _normalize_listing_volume(competitor_count: int) -> float:
    """More listings = more liquid market = better.
    Uses log scale: 1→20, 3→48, 7→68, 15→82, 30→92."""
    if competitor_count <= 0:
        return 0
    return min(100, 20 + 25 * math.log2(competitor_count + 1))


def _normalize_seller_reputation(
    buy_rating: float | None,
    sell_rating: float | None,
    buy_reviews: int,
    sell_reviews: int,
) -> float:
    """Combined buyer/seller reputation. Considers both rating and review volume."""
    scores = []
    for rating, reviews in [(buy_rating, buy_reviews), (sell_rating, sell_reviews)]:
        if rating is not None and rating > 0:
            # Rating component (0-5 scale → 0-100)
            rating_norm = (rating / 5.0) * 100
            # Review confidence boost: more reviews = more reliable rating
            review_boost = min(20, math.log2(reviews + 1) * 5) if reviews > 0 else 0
            scores.append(min(100, rating_norm * 0.8 + review_boost))
        elif reviews > 0:
            # No rating but has reviews — give partial credit
            scores.append(min(60, reviews * 2))
        else:
            scores.append(30)  # Unknown seller baseline

    return statistics.mean(scores)


def _normalize_demand(
    sales_count: int,
    reviews_count: int,
    estimated_daily_sales: float,
    market_depth_score: float,
) -> float:
    """Marketplace demand from sales signals.
    Combines observed sales, reviews as proxy, and depth estimation.

    v3.1: Reviews are more valuable than before — most scraped listings have
    reviews but not direct sales data. A product with 100+ reviews has proven demand.
    """
    # Direct sales signal (diminishing returns)
    sales_signal = min(100, 100 * (1 - math.exp(-0.01 * sales_count))) if sales_count > 0 else 0

    # Reviews as demand proxy — stronger signal than before
    # 10 reviews → ~45, 50 → ~78, 100 → ~92, 500 → ~100
    review_signal = min(100, 100 * (1 - math.exp(-0.025 * reviews_count))) if reviews_count > 0 else 0

    # Model-estimated daily sales
    daily_signal = min(100, estimated_daily_sales * 20) if estimated_daily_sales > 0 else 0

    # Weighted combination — prefer direct evidence
    if sales_count > 0:
        return sales_signal * 0.35 + review_signal * 0.25 + daily_signal * 0.20 + market_depth_score * 0.20
    elif reviews_count > 0:
        # v3.1: Reviews are still a strong signal — don't penalize as much
        return review_signal * 0.40 + daily_signal * 0.30 + market_depth_score * 0.30
    else:
        return daily_signal * 0.50 + market_depth_score * 0.50


def _normalize_volatility(stability_score: float) -> float:
    """Stability score already 0-100 from price_history CV analysis.
    High stability = low volatility = good for opportunity."""
    return max(0, min(100, stability_score))


def _normalize_sales_frequency(
    total_sales: int,
    total_reviews: int,
    listing_age_days: float,
) -> float:
    """Historical sales frequency — how often this product actually sells."""
    if listing_age_days <= 0:
        listing_age_days = 1.0

    # Sales per day
    daily_rate = total_sales / listing_age_days if total_sales > 0 else 0
    # Reviews per day as proxy
    review_rate = (total_reviews / listing_age_days) if total_reviews > 0 else 0
    # Estimated actual sales (1 review ≈ 33 purchases)
    estimated_daily = daily_rate + (review_rate * 33)

    # Normalize: 1 sale/day → ~63, 5/day → ~90, 10/day → ~97
    if estimated_daily <= 0:
        return 10  # Low baseline for unknown
    return min(100, 100 * (1 - math.exp(-0.5 * estimated_daily)))


# ── Scoring Weights (v3: rebalanced for reseller focus) ────

# Opportunity score weights (sum = 1.0)
OPP_WEIGHTS = {
    "price_margin": 0.20,        # Profit potential
    "marketplace_demand": 0.25,  # Will it sell? (highest weight)
    "historical_sales": 0.20,    # Proven sales track record
    "price_volatility": 0.10,    # Price stability
    "listing_volume": 0.10,      # Market liquidity / competition
    "seller_reputation": 0.10,   # Trust in counterparties
    "price_stability": 0.05,     # CV-based price consistency
}

# Risk score weights (sum = 1.0)
RISK_WEIGHTS = {
    "volatility_risk": 0.25,    # Price might change before you sell
    "competition_risk": 0.20,   # Others competing on same opportunity
    "liquidity_risk": 0.20,     # Might not find a buyer quickly
    "margin_risk": 0.15,        # Thin margins vulnerable to fee changes
    "cross_border_risk": 0.20,  # Import/shipping complications
}

# Confidence score components
CONFIDENCE_FACTORS = {
    "data_points": 0.40,      # How much data do we have?
    "signal_agreement": 0.35,  # Do all signals point the same direction?
    "recency": 0.25,          # How fresh is the data?
}


# ── Input data structure ────────────────────────────────────

@dataclass
class ScoringInput:
    """All raw signals needed for scoring. Gathered by the opportunity engine."""
    # Price/profit
    net_profit_usd: float
    roi: float
    margin: float  # net_profit / sell_price
    buy_price_usd: float

    # Listing volume
    competitor_count: int
    total_listings: int  # All listings for this product across marketplaces

    # Seller reputation
    buy_seller_rating: float | None
    buy_seller_reviews: int
    sell_seller_rating: float | None
    sell_seller_reviews: int

    # Marketplace demand
    total_sales_count: int  # Sum of sales_count across sell-side listings
    total_reviews_count: int
    estimated_daily_sales: float
    market_depth_score: float

    # Price volatility
    price_stability_score: float  # From price_history CV (0-100)

    # Historical sales
    listing_age_days: float  # Age of oldest listing for this product

    # Context flags
    is_cross_border: bool
    price_spread_pct: float  # (max - min) / avg across sell listings
    route_difficulty: int = 1  # 1=easy, 2=medium, 3=hard

    # v3: Soft penalties from opportunity engine
    soft_penalty: float = 0.0         # Deducted from opportunity_score
    confidence_penalty: float = 0.0   # Deducted from confidence_score


@dataclass
class ScoringOutput:
    """Three-score output."""
    opportunity_score: float   # 0-100: overall ranking
    risk_score: float          # 0-100: downside probability (lower = safer)
    confidence_score: float    # 0-100: data reliability (higher = better)
    confidence_level: str      # "high" / "medium" / "low"

    # Breakdown for transparency
    factors: dict[str, float]  # Each normalized factor (0-100)


# ── Core scoring functions ──────────────────────────────────

def compute_opportunity_score(inp: ScoringInput) -> float:
    """
    opportunity_score = Σ(normalized_factor × weight) - soft_penalty

    Factors:
      price_margin:       margin normalized (50% = 100)
      marketplace_demand: sales + reviews + depth estimation
      historical_sales:   sales frequency relative to listing age
      price_volatility:   stability from CV of price_history
      listing_volume:     log-scaled competitor count
      seller_reputation:  weighted avg of buy/sell ratings + review volume
      price_stability:    same as volatility (CV-based, secondary weight)
    """
    factors = _compute_factors(inp)

    score = sum(
        factors[name] * weight
        for name, weight in OPP_WEIGHTS.items()
    )

    # Bonus: high profit + high demand = exceptional opportunity
    if factors["price_margin"] > 70 and factors["marketplace_demand"] > 60:
        score = min(100, score * 1.08)

    # Penalty: extremely low volume = illiquid, risky even if profitable
    if factors["listing_volume"] < 15:
        score *= 0.90  # v3: softer penalty (was 0.85)

    # v3: Apply soft penalties from opportunity engine
    score -= inp.soft_penalty

    return round(max(0, min(100, score)), 1)


def compute_risk_score(inp: ScoringInput) -> float:
    """
    risk_score (0-100, lower = safer):

      volatility_risk:    100 - stability_score (unstable prices = risky)
      competition_risk:   tight spread + many competitors = price war risk
      liquidity_risk:     low demand = hard to sell
      margin_risk:        thin margin = vulnerable to fee/price changes
      cross_border_risk:  import tax, customs, longer shipping = more risk
    """
    factors = _compute_factors(inp)

    # Volatility risk: inverse of stability
    volatility_risk = 100 - factors["price_volatility"]

    # Competition risk: many competitors with tight spread
    if inp.competitor_count >= 10 and inp.price_spread_pct < 0.10:
        competition_risk = 85  # Price war zone
    elif inp.competitor_count >= 10:
        competition_risk = 65
    elif inp.competitor_count >= 5:
        competition_risk = 45
    elif inp.competitor_count >= 2:
        competition_risk = 25
    else:
        competition_risk = 80  # Single listing = no price discovery = risky

    # Liquidity risk: inverse of demand
    liquidity_risk = max(0, 100 - factors["marketplace_demand"])

    # Margin risk: v3 adjusted tiers for realistic arbitrage (3-8% margins are common)
    if inp.margin > 0.40:
        margin_risk = 10
    elif inp.margin > 0.25:
        margin_risk = 25
    elif inp.margin > 0.15:
        margin_risk = 40
    elif inp.margin > 0.08:
        margin_risk = 55
    elif inp.margin > 0.05:
        margin_risk = 70  # Thin but viable for volume resellers
    else:
        margin_risk = 85  # Very thin, high risk

    # Cross-border risk — v3: scaled down to avoid over-penalizing
    if not inp.is_cross_border:
        cross_border_risk = 10   # Domestic
    elif inp.route_difficulty >= 3:
        cross_border_risk = 70   # Hard routes (was 85)
    elif inp.route_difficulty >= 2:
        cross_border_risk = 50   # Medium routes (was 60)
    else:
        cross_border_risk = 30   # Easy cross-border (was 40)

    risk = (
        volatility_risk * RISK_WEIGHTS["volatility_risk"]
        + competition_risk * RISK_WEIGHTS["competition_risk"]
        + liquidity_risk * RISK_WEIGHTS["liquidity_risk"]
        + margin_risk * RISK_WEIGHTS["margin_risk"]
        + cross_border_risk * RISK_WEIGHTS["cross_border_risk"]
    )

    # Outlier ROI penalty: if ROI > 200%, something might be wrong
    if inp.roi > 2.0:
        risk = min(100, risk + 10)  # v3: softer penalty (was +15)

    # v3: Low seller rating increases risk
    for rating in [inp.buy_seller_rating, inp.sell_seller_rating]:
        if rating is not None and rating < 3.0:
            risk = min(100, risk + 5)

    return round(max(0, min(100, risk)), 1)


def compute_confidence_score(inp: ScoringInput) -> tuple[float, str]:
    """
    confidence_score (0-100):

      data_points:       how many data signals do we have?
      signal_agreement:  do independent signals agree?
      recency:           how fresh is our data?

    Returns (score, level).
    """
    # Data coverage: count how many signals are non-default
    # v3.1: Use weighted signals — some are more valuable than others
    signal_weights = {
        "buy_rating": (inp.buy_seller_rating is not None, 1.0),
        "sell_rating": (inp.sell_seller_rating is not None, 1.0),
        "sales": (inp.total_sales_count > 0, 1.5),         # Sales data is high-value
        "reviews": (inp.total_reviews_count > 0, 1.5),      # Reviews are high-value
        "stability": (inp.price_stability_score != 50.0, 1.0),
        "daily_sales": (inp.estimated_daily_sales > 0, 1.0),
        "listing_age": (inp.listing_age_days > 1.0, 0.5),   # Less important
        "multi_listing": (inp.total_listings >= 3, 1.5),     # Multiple listings = strong signal
    }

    weighted_available = sum(w for avail, w in signal_weights.values() if avail)
    weighted_total = sum(w for _, w in signal_weights.values())
    data_coverage = (weighted_available / weighted_total) * 100

    # Signal agreement: check if positive signals align
    factors = _compute_factors(inp)
    # v3.1: Count factors > 40 (not 50) — many valid signals land 30-50
    positive_factors = [v for v in factors.values() if v > 40]

    # Strong agreement = most factors point the same direction
    if len(positive_factors) >= 5:
        signal_agreement = 90
    elif len(positive_factors) >= 4:
        signal_agreement = 75
    elif len(positive_factors) >= 3:
        signal_agreement = 60
    else:
        # Mixed signals = low confidence
        signal_agreement = 35

    # Consistency: low variance among factors = signals agree on magnitude
    factor_values = list(factors.values())
    if len(factor_values) >= 2:
        mean_val = max(statistics.mean(factor_values), 1)
        cv = statistics.stdev(factor_values) / mean_val
        consistency_bonus = max(0, (1 - cv) * 20)  # Up to 20 bonus for consistency
        signal_agreement = min(100, signal_agreement + consistency_bonus)

    # Recency: more listings = fresher data (proxy)
    if inp.total_listings >= 8:
        recency = 95
    elif inp.total_listings >= 5:
        recency = 80
    elif inp.total_listings >= 3:
        recency = 65
    elif inp.total_listings >= 2:
        recency = 50
    else:
        recency = 25

    confidence = (
        data_coverage * CONFIDENCE_FACTORS["data_points"]
        + signal_agreement * CONFIDENCE_FACTORS["signal_agreement"]
        + recency * CONFIDENCE_FACTORS["recency"]
    )

    # v3: Apply confidence penalty from opportunity engine (high price ratio, etc.)
    confidence -= inp.confidence_penalty

    confidence = round(max(0, min(100, confidence)), 1)

    # v3.1: Adjusted thresholds — 70 was unreachable with typical marketplace data
    level = "high" if confidence >= 60 else "medium" if confidence >= 40 else "low"
    return confidence, level


# ── Factor computation (shared) ─────────────────────────────

def _compute_factors(inp: ScoringInput) -> dict[str, float]:
    """Compute all 7 normalized factors from raw input."""
    volatility = _normalize_volatility(inp.price_stability_score)
    return {
        "price_margin": _normalize_margin(inp.margin),
        "marketplace_demand": _normalize_demand(
            inp.total_sales_count, inp.total_reviews_count,
            inp.estimated_daily_sales, inp.market_depth_score,
        ),
        "historical_sales": _normalize_sales_frequency(
            inp.total_sales_count, inp.total_reviews_count,
            inp.listing_age_days,
        ),
        "price_volatility": volatility,
        "listing_volume": _normalize_listing_volume(inp.competitor_count),
        "seller_reputation": _normalize_seller_reputation(
            inp.buy_seller_rating, inp.sell_seller_rating,
            inp.buy_seller_reviews, inp.sell_seller_reviews,
        ),
        "price_stability": volatility,  # Secondary weight on same signal
    }


# ── Public API ──────────────────────────────────────────────

def score(inp: ScoringInput) -> ScoringOutput:
    """
    Main entry point. Compute all three scores from raw signals.

    Usage:
        from api.scoring import score, ScoringInput

        inp = ScoringInput(
            net_profit_usd=45.0, roi=0.35, margin=0.28,
            buy_price_usd=128.0, competitor_count=8, total_listings=15,
            buy_seller_rating=4.5, buy_seller_reviews=120,
            sell_seller_rating=4.2, sell_seller_reviews=85,
            total_sales_count=50, total_reviews_count=30,
            estimated_daily_sales=2.5, market_depth_score=65.0,
            price_stability_score=72.0, listing_age_days=30.0,
            is_cross_border=False, price_spread_pct=0.15,
        )
        result = score(inp)
        # result.opportunity_score = 68.5
        # result.risk_score = 34.2
        # result.confidence_score = 75.0
    """
    opp_score = compute_opportunity_score(inp)
    risk = compute_risk_score(inp)
    confidence, level = compute_confidence_score(inp)
    factors = _compute_factors(inp)

    return ScoringOutput(
        opportunity_score=opp_score,
        risk_score=risk,
        confidence_score=confidence,
        confidence_level=level,
        factors=factors,
    )
