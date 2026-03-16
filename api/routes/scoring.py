"""
Scoring Profiles API — configure and test scoring strategies.

Endpoints:
  GET  /scoring/profiles          — List available scoring profiles
  POST /scoring/test              — Test score an opportunity with a specific profile
"""
from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..scoring import ScoringInput, score as score_default
from ..scoring_profiles import (
    PROFILES,
    ScoringProfile,
    RiskClass,
    score_with_profile,
    classify_risk,
)

router = APIRouter(prefix="/scoring", tags=["scoring"])


@router.get("/profiles")
async def list_profiles():
    """List all available scoring profiles with their weight configurations."""
    return {
        "profiles": [
            {
                "name": config.name,
                "description": config.description,
                "opp_weights": config.opp_weights,
                "risk_weights": config.risk_weights,
                "thresholds": {
                    "prime_min_opp": config.prime_min_opp,
                    "prime_max_risk": config.prime_max_risk,
                    "prime_min_conf": config.prime_min_conf,
                    "standard_min_opp": config.standard_min_opp,
                    "standard_max_risk": config.standard_max_risk,
                    "avoid_max_opp": config.avoid_max_opp,
                },
            }
            for config in PROFILES.values()
        ]
    }


class ScoreTestRequest(BaseModel):
    net_profit_usd: float
    roi: float
    margin: float
    buy_price_usd: float
    competitor_count: int = 5
    total_listings: int = 10
    buy_seller_rating: float | None = None
    buy_seller_reviews: int = 0
    sell_seller_rating: float | None = None
    sell_seller_reviews: int = 0
    total_sales_count: int = 0
    total_reviews_count: int = 0
    estimated_daily_sales: float = 0.0
    market_depth_score: float = 50.0
    price_stability_score: float = 50.0
    listing_age_days: float = 7.0
    is_cross_border: bool = False
    price_spread_pct: float = 0.15
    route_difficulty: int = 1
    profile: str = "balanced"


@router.post("/test")
async def test_scoring(req: ScoreTestRequest):
    """Test score an opportunity with a specific profile."""
    inp = ScoringInput(
        net_profit_usd=req.net_profit_usd,
        roi=req.roi,
        margin=req.margin,
        buy_price_usd=req.buy_price_usd,
        competitor_count=req.competitor_count,
        total_listings=req.total_listings,
        buy_seller_rating=req.buy_seller_rating,
        buy_seller_reviews=req.buy_seller_reviews,
        sell_seller_rating=req.sell_seller_rating,
        sell_seller_reviews=req.sell_seller_reviews,
        total_sales_count=req.total_sales_count,
        total_reviews_count=req.total_reviews_count,
        estimated_daily_sales=req.estimated_daily_sales,
        market_depth_score=req.market_depth_score,
        price_stability_score=req.price_stability_score,
        listing_age_days=req.listing_age_days,
        is_cross_border=req.is_cross_border,
        price_spread_pct=req.price_spread_pct,
        route_difficulty=req.route_difficulty,
    )

    result, risk_class = score_with_profile(inp, req.profile)

    return {
        "profile": req.profile,
        "opportunity_score": result.opportunity_score,
        "risk_score": result.risk_score,
        "confidence_score": result.confidence_score,
        "confidence_level": result.confidence_level,
        "risk_class": risk_class.value,
        "factors": result.factors,
    }
