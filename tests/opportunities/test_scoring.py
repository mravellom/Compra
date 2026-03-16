"""
Unit Tests — Opportunity Scoring System v3.

Tests all three scores:
  - opportunity_score (0-100)
  - risk_score (0-100)
  - confidence_score (0-100)

Tests normalization functions, weight composition,
bonuses, penalties, and edge cases.
"""
import math

import pytest

from api.scoring import (
    ScoringInput,
    ScoringOutput,
    score,
    compute_opportunity_score,
    compute_risk_score,
    compute_confidence_score,
    _normalize_margin,
    _normalize_roi,
    _normalize_profit,
    _normalize_listing_volume,
    _normalize_seller_reputation,
    _normalize_demand,
    _normalize_volatility,
    _normalize_sales_frequency,
    _compute_factors,
    OPP_WEIGHTS,
    RISK_WEIGHTS,
)


# Import factory from fixtures
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fixtures.conftest import make_scoring_input


# ═══════════════════════════════════════════════════════════════
# 1. Normalization Functions
# ═══════════════════════════════════════════════════════════════


class TestNormalizeMargin:

    def test_zero(self):
        assert _normalize_margin(0.0) == 0

    def test_50_percent_is_100(self):
        assert _normalize_margin(0.50) == 100

    def test_25_percent(self):
        assert _normalize_margin(0.25) == 50

    def test_caps_at_100(self):
        assert _normalize_margin(1.0) == 100

    def test_negative(self):
        assert _normalize_margin(-0.1) == 0


class TestNormalizeROI:

    def test_zero(self):
        assert _normalize_roi(0.0) == 0

    def test_negative(self):
        assert _normalize_roi(-0.5) == 0

    def test_50_percent_high(self):
        result = _normalize_roi(0.50)
        assert 70 < result < 85

    def test_100_percent_higher(self):
        result = _normalize_roi(1.0)
        assert result > _normalize_roi(0.50)

    def test_diminishing_returns(self):
        """Double ROI should not double the score."""
        score_50 = _normalize_roi(0.50)
        score_100 = _normalize_roi(1.00)
        improvement = score_100 - score_50
        assert improvement < score_50  # Diminishing returns


class TestNormalizeProfit:

    def test_zero(self):
        assert _normalize_profit(0.0) == 0

    def test_50_usd(self):
        result = _normalize_profit(50.0)
        assert 55 < result < 80

    def test_100_usd(self):
        result = _normalize_profit(100.0)
        assert result > 85

    def test_caps_at_100(self):
        assert _normalize_profit(10000.0) == 100


class TestNormalizeListingVolume:

    def test_zero(self):
        assert _normalize_listing_volume(0) == 0

    def test_single_listing(self):
        result = _normalize_listing_volume(1)
        assert result == 20 + 25 * math.log2(2)

    def test_increases_with_count(self):
        assert _normalize_listing_volume(10) > _normalize_listing_volume(5)

    def test_caps_at_100(self):
        assert _normalize_listing_volume(10000) == 100


class TestNormalizeSellerReputation:

    def test_perfect_rating(self):
        result = _normalize_seller_reputation(5.0, 5.0, 100, 100)
        assert result > 80

    def test_no_ratings(self):
        result = _normalize_seller_reputation(None, None, 0, 0)
        assert result == 30  # Unknown seller baseline

    def test_one_side_rated(self):
        result = _normalize_seller_reputation(4.5, None, 50, 0)
        # One rated (high) + one unknown (30) → avg
        assert 30 < result < 90

    def test_low_rating(self):
        result = _normalize_seller_reputation(2.0, 2.0, 10, 10)
        assert result < 60


class TestNormalizeDemand:

    def test_high_demand_with_sales(self):
        result = _normalize_demand(
            sales_count=100, reviews_count=50,
            estimated_daily_sales=5.0, market_depth_score=80.0,
        )
        assert result > 60

    def test_no_demand_signals(self):
        result = _normalize_demand(
            sales_count=0, reviews_count=0,
            estimated_daily_sales=0.0, market_depth_score=0.0,
        )
        assert result == 0

    def test_reviews_only(self):
        """Reviews without sales should still produce meaningful demand."""
        result = _normalize_demand(
            sales_count=0, reviews_count=100,
            estimated_daily_sales=0.0, market_depth_score=50.0,
        )
        assert result > 30


class TestNormalizeSalesFrequency:

    def test_high_frequency(self):
        result = _normalize_sales_frequency(
            total_sales=100, total_reviews=50, listing_age_days=30.0,
        )
        assert result > 50

    def test_no_sales(self):
        result = _normalize_sales_frequency(
            total_sales=0, total_reviews=0, listing_age_days=30.0,
        )
        assert result == 10  # Low baseline

    def test_zero_age_defaults_to_1(self):
        """listing_age_days=0 should not cause division error."""
        result = _normalize_sales_frequency(
            total_sales=10, total_reviews=5, listing_age_days=0.0,
        )
        assert result > 0


# ═══════════════════════════════════════════════════════════════
# 2. Opportunity Score Composition
# ═══════════════════════════════════════════════════════════════


class TestOpportunityScore:

    def test_all_factors_produce_positive_score(self):
        inp = make_scoring_input()
        result = compute_opportunity_score(inp)
        assert 0 <= result <= 100

    def test_better_inputs_produce_higher_score(self):
        weak = make_scoring_input(roi=0.05, margin=0.03, net_profit_usd=5.0)
        strong = make_scoring_input(roi=0.50, margin=0.35, net_profit_usd=80.0)
        assert compute_opportunity_score(strong) > compute_opportunity_score(weak)

    def test_bonus_high_margin_and_demand(self):
        """High margin + high demand triggers 1.08x bonus."""
        inp = make_scoring_input(
            margin=0.50,  # → 100 normalized
            total_sales_count=200, total_reviews_count=100,
            estimated_daily_sales=10.0, market_depth_score=90.0,
        )
        result = compute_opportunity_score(inp)
        assert result > 60

    def test_low_volume_penalty(self):
        """listing_volume < 15 triggers 0.90 penalty."""
        inp = make_scoring_input(competitor_count=0)
        factors = _compute_factors(inp)
        assert factors["listing_volume"] < 15

    def test_soft_penalty_deducted(self):
        base = make_scoring_input(soft_penalty=0.0)
        penalized = make_scoring_input(soft_penalty=15.0)
        assert compute_opportunity_score(penalized) < compute_opportunity_score(base)

    def test_weights_sum_to_one(self):
        total = sum(OPP_WEIGHTS.values())
        assert total == pytest.approx(1.0, abs=0.001)


# ═══════════════════════════════════════════════════════════════
# 3. Risk Score
# ═══════════════════════════════════════════════════════════════


class TestRiskScore:

    def test_low_risk_scenario(self):
        inp = make_scoring_input(
            margin=0.45,
            price_stability_score=90.0,
            competitor_count=5,
            is_cross_border=False,
        )
        result = compute_risk_score(inp)
        assert result < 50

    def test_high_risk_scenario(self):
        inp = make_scoring_input(
            margin=0.02,
            price_stability_score=10.0,
            competitor_count=15,
            price_spread_pct=0.05,
            is_cross_border=True,
            route_difficulty=3,
        )
        result = compute_risk_score(inp)
        assert result > 50

    def test_domestic_lower_risk_than_cross_border(self):
        domestic = make_scoring_input(is_cross_border=False)
        cross = make_scoring_input(is_cross_border=True, route_difficulty=3)
        assert compute_risk_score(domestic) < compute_risk_score(cross)

    def test_outlier_roi_penalty(self):
        """ROI > 200% adds risk penalty."""
        normal = make_scoring_input(roi=0.50)
        outlier = make_scoring_input(roi=2.50)
        assert compute_risk_score(outlier) > compute_risk_score(normal)

    def test_low_seller_rating_adds_risk(self):
        good = make_scoring_input(buy_seller_rating=4.5)
        bad = make_scoring_input(buy_seller_rating=2.0)
        assert compute_risk_score(bad) > compute_risk_score(good)

    def test_risk_weights_sum_to_one(self):
        total = sum(RISK_WEIGHTS.values())
        assert total == pytest.approx(1.0, abs=0.001)


# ═══════════════════════════════════════════════════════════════
# 4. Confidence Score
# ═══════════════════════════════════════════════════════════════


class TestConfidenceScore:

    def test_all_data_present_high_confidence(self):
        inp = make_scoring_input(
            total_sales_count=100, total_reviews_count=200,
            total_listings=10,
        )
        conf, level = compute_confidence_score(inp)
        assert conf > 50
        assert level in ("high", "medium")

    def test_minimal_data_low_confidence(self):
        inp = make_scoring_input(
            buy_seller_rating=None, sell_seller_rating=None,
            total_sales_count=0, total_reviews_count=0,
            estimated_daily_sales=0.0,
            price_stability_score=50.0,
            total_listings=1,
            listing_age_days=0.5,
        )
        conf, level = compute_confidence_score(inp)
        assert conf < 60

    def test_confidence_penalty_deducted(self):
        base = make_scoring_input(confidence_penalty=0.0)
        penalized = make_scoring_input(confidence_penalty=20.0)
        c_base, _ = compute_confidence_score(base)
        c_pen, _ = compute_confidence_score(penalized)
        assert c_pen < c_base

    def test_confidence_levels(self):
        high_inp = make_scoring_input(
            total_sales_count=200, total_reviews_count=500,
            total_listings=20, estimated_daily_sales=5.0,
        )
        _, level = compute_confidence_score(high_inp)
        assert level in ("high", "medium", "low")


# ═══════════════════════════════════════════════════════════════
# 5. Full Score Integration
# ═══════════════════════════════════════════════════════════════


class TestScoreIntegration:

    def test_returns_scoring_output(self):
        inp = make_scoring_input()
        result = score(inp)
        assert isinstance(result, ScoringOutput)
        assert 0 <= result.opportunity_score <= 100
        assert 0 <= result.risk_score <= 100
        assert 0 <= result.confidence_score <= 100
        assert result.confidence_level in ("high", "medium", "low")

    def test_factors_dict_contains_all_keys(self):
        inp = make_scoring_input()
        result = score(inp)
        expected_keys = {
            "price_margin", "marketplace_demand", "historical_sales",
            "price_volatility", "listing_volume", "seller_reputation",
            "price_stability",
        }
        assert set(result.factors.keys()) == expected_keys

    def test_all_factors_bounded(self):
        inp = make_scoring_input()
        result = score(inp)
        for name, value in result.factors.items():
            assert 0 <= value <= 100, f"Factor {name} out of bounds: {value}"
