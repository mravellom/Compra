"""
Opportunity Validation Filters — High-precision gate.

Every opportunity must pass ALL filters to be considered valid.
No short-circuiting: all filters run so rejection reasons are fully collected.

Designed as a wrapper layer — does not modify existing opportunity logic.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class FilterConfig:
    """All filter thresholds in one place. No magic numbers."""
    min_confidence_score: float = 85.0
    min_profit_usd: float = 20.0
    min_roi: float = 0.15               # 15%
    min_seller_rating: float = 4.2
    min_reviews_count: int = 50
    min_monthly_sales: int = 5
    max_price_variation_pct: float = 0.10  # 10%
    min_lifespan_hours: float = 4.0
    # Liquidity: pass if monthly_sales >= threshold OR reviews >= this
    liquidity_reviews_fallback: int = 100


@dataclass
class OpportunitySnapshot:
    """All data needed for validation — decoupled from ORM models."""
    confidence_score: float
    net_profit_usd: float
    roi: float
    buy_seller_rating: float | None
    sell_seller_rating: float | None
    buy_reviews_count: int
    sell_reviews_count: int
    estimated_monthly_sales: float
    price_spread_pct: float
    listing_created_at: datetime | None = None
    adjusted_profit_usd: float | None = None
    adjusted_roi: float | None = None


@dataclass
class FilterResult:
    """Complete validation result with all reasons collected."""
    is_valid: bool
    rejection_reasons: list[str]
    match_confidence: float
    liquidity_score: float
    estimated_profit: float
    adjusted_profit: float | None
    roi: float
    adjusted_roi: float | None
    passed_filters: dict[str, bool]


# ── Individual filter functions ──────────────────────────────


def _check_confidence(
    snapshot: OpportunitySnapshot, config: FilterConfig,
) -> tuple[bool, str]:
    if snapshot.confidence_score >= config.min_confidence_score:
        return True, ""
    return False, (
        f"confidence {snapshot.confidence_score:.1f} < {config.min_confidence_score}"
    )


def _check_profit(
    snapshot: OpportunitySnapshot, config: FilterConfig,
) -> tuple[bool, str]:
    profit = snapshot.adjusted_profit_usd if snapshot.adjusted_profit_usd is not None else snapshot.net_profit_usd
    if profit >= config.min_profit_usd:
        return True, ""
    return False, f"profit ${profit:.2f} < ${config.min_profit_usd:.2f}"


def _check_roi(
    snapshot: OpportunitySnapshot, config: FilterConfig,
) -> tuple[bool, str]:
    roi = snapshot.adjusted_roi if snapshot.adjusted_roi is not None else snapshot.roi
    if roi >= config.min_roi:
        return True, ""
    return False, f"roi {roi:.1%} < {config.min_roi:.0%}"


def _check_seller_rating(
    snapshot: OpportunitySnapshot, config: FilterConfig,
) -> tuple[bool, str]:
    ratings = []
    if snapshot.buy_seller_rating is not None:
        ratings.append(("buy", snapshot.buy_seller_rating))
    if snapshot.sell_seller_rating is not None:
        ratings.append(("sell", snapshot.sell_seller_rating))

    if not ratings:
        # No rating data — fail conservatively
        return False, "no seller rating data available"

    for side, rating in ratings:
        if rating < config.min_seller_rating:
            return False, (
                f"{side}_seller_rating {rating:.1f} < {config.min_seller_rating}"
            )
    return True, ""


def _check_reviews(
    snapshot: OpportunitySnapshot, config: FilterConfig,
) -> tuple[bool, str]:
    total_reviews = snapshot.buy_reviews_count + snapshot.sell_reviews_count
    if total_reviews >= config.min_reviews_count:
        return True, ""
    return False, f"total_reviews {total_reviews} < {config.min_reviews_count}"


def _check_liquidity(
    snapshot: OpportunitySnapshot, config: FilterConfig,
) -> tuple[bool, str]:
    """Pass if monthly_sales >= threshold OR total reviews >= fallback."""
    if snapshot.estimated_monthly_sales >= config.min_monthly_sales:
        return True, ""
    total_reviews = snapshot.buy_reviews_count + snapshot.sell_reviews_count
    if total_reviews >= config.liquidity_reviews_fallback:
        return True, ""
    return False, (
        f"liquidity: monthly_sales {snapshot.estimated_monthly_sales:.1f} < {config.min_monthly_sales} "
        f"AND reviews {total_reviews} < {config.liquidity_reviews_fallback}"
    )


def _check_price_consistency(
    snapshot: OpportunitySnapshot, config: FilterConfig,
) -> tuple[bool, str]:
    if snapshot.price_spread_pct <= config.max_price_variation_pct:
        return True, ""
    return False, (
        f"price_spread {snapshot.price_spread_pct:.1%} > {config.max_price_variation_pct:.0%}"
    )


def _check_time_validity(
    snapshot: OpportunitySnapshot, config: FilterConfig,
) -> tuple[bool, str]:
    if snapshot.listing_created_at is None:
        # No timestamp — pass (don't reject on missing data for time)
        return True, ""
    age_hours = (
        datetime.now(timezone.utc) - snapshot.listing_created_at
    ).total_seconds() / 3600.0
    if age_hours >= config.min_lifespan_hours:
        return True, ""
    return False, (
        f"listing_age {age_hours:.1f}h < {config.min_lifespan_hours}h"
    )


# ── Liquidity Score ──────────────────────────────────────────


def compute_liquidity_score(
    monthly_sales: float,
    total_reviews: int,
) -> float:
    """Compute 0-100 liquidity score from sales and review signals."""
    # Sales component (0-60): 30 monthly sales = max
    sales_score = min(60.0, (monthly_sales / 30.0) * 60.0)

    # Reviews component (0-40): 200 reviews = max
    reviews_score = min(40.0, (total_reviews / 200.0) * 40.0)

    return round(sales_score + reviews_score, 1)


# ── Main Validation ──────────────────────────────────────────

_FILTERS = [
    ("confidence", _check_confidence),
    ("profit", _check_profit),
    ("roi", _check_roi),
    ("seller_rating", _check_seller_rating),
    ("reviews", _check_reviews),
    ("liquidity", _check_liquidity),
    ("price_consistency", _check_price_consistency),
    ("time_validity", _check_time_validity),
]


def validate_opportunity(
    snapshot: OpportunitySnapshot,
    config: FilterConfig | None = None,
) -> FilterResult:
    """Run ALL filters (no short-circuit) and collect all rejection reasons."""
    cfg = config or FilterConfig()

    passed_filters: dict[str, bool] = {}
    rejection_reasons: list[str] = []

    for name, check_fn in _FILTERS:
        passed, reason = check_fn(snapshot, cfg)
        passed_filters[name] = passed
        if not passed:
            rejection_reasons.append(reason)

    total_reviews = snapshot.buy_reviews_count + snapshot.sell_reviews_count
    liquidity = compute_liquidity_score(
        snapshot.estimated_monthly_sales, total_reviews,
    )

    is_valid = len(rejection_reasons) == 0

    if not is_valid:
        logger.debug(
            "opportunity rejected (%d reasons): %s",
            len(rejection_reasons), "; ".join(rejection_reasons),
        )

    return FilterResult(
        is_valid=is_valid,
        rejection_reasons=rejection_reasons,
        match_confidence=snapshot.confidence_score,
        liquidity_score=liquidity,
        estimated_profit=snapshot.net_profit_usd,
        adjusted_profit=snapshot.adjusted_profit_usd,
        roi=snapshot.roi,
        adjusted_roi=snapshot.adjusted_roi,
        passed_filters=passed_filters,
    )
