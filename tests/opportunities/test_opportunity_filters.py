"""
Tests — Opportunity Validation Filters.

Verifies each individual filter and the composite validate_opportunity function.
"""
from datetime import datetime, timedelta, timezone

import pytest

from api.opportunity_filters import (
    FilterConfig,
    FilterResult,
    OpportunitySnapshot,
    compute_liquidity_score,
    validate_opportunity,
    _check_confidence,
    _check_profit,
    _check_roi,
    _check_seller_rating,
    _check_reviews,
    _check_liquidity,
    _check_price_consistency,
    _check_time_validity,
)
from tests.fixtures.conftest import make_opportunity_snapshot


class TestFilterConfig:

    def test_defaults(self):
        cfg = FilterConfig()
        assert cfg.min_confidence_score == 85.0
        assert cfg.min_profit_usd == 20.0
        assert cfg.min_roi == 0.15
        assert cfg.min_seller_rating == 4.2
        assert cfg.min_reviews_count == 50
        assert cfg.min_monthly_sales == 5
        assert cfg.max_price_variation_pct == 0.10
        assert cfg.min_lifespan_hours == 4.0


class TestCheckConfidence:

    def test_passes_above_threshold(self):
        snap = make_opportunity_snapshot(confidence_score=90.0)
        ok, _ = _check_confidence(snap, FilterConfig())
        assert ok is True

    def test_fails_below_threshold(self):
        snap = make_opportunity_snapshot(confidence_score=70.0)
        ok, reason = _check_confidence(snap, FilterConfig())
        assert ok is False
        assert "confidence" in reason


class TestCheckProfit:

    def test_passes_with_good_profit(self):
        snap = make_opportunity_snapshot(net_profit_usd=45.0)
        ok, _ = _check_profit(snap, FilterConfig())
        assert ok is True

    def test_uses_adjusted_profit_if_available(self):
        snap = make_opportunity_snapshot(net_profit_usd=45.0, adjusted_profit_usd=10.0)
        ok, reason = _check_profit(snap, FilterConfig())
        assert ok is False
        assert "$10.00" in reason

    def test_fails_below_threshold(self):
        snap = make_opportunity_snapshot(net_profit_usd=5.0)
        ok, _ = _check_profit(snap, FilterConfig())
        assert ok is False


class TestCheckROI:

    def test_passes_good_roi(self):
        snap = make_opportunity_snapshot(roi=0.35)
        ok, _ = _check_roi(snap, FilterConfig())
        assert ok is True

    def test_uses_adjusted_roi(self):
        snap = make_opportunity_snapshot(roi=0.35, adjusted_roi=0.05)
        ok, _ = _check_roi(snap, FilterConfig())
        assert ok is False

    def test_fails_below_threshold(self):
        snap = make_opportunity_snapshot(roi=0.05)
        ok, _ = _check_roi(snap, FilterConfig())
        assert ok is False


class TestCheckSellerRating:

    def test_passes_good_ratings(self):
        snap = make_opportunity_snapshot(buy_seller_rating=4.5, sell_seller_rating=4.3)
        ok, _ = _check_seller_rating(snap, FilterConfig())
        assert ok is True

    def test_fails_low_buy_rating(self):
        snap = make_opportunity_snapshot(buy_seller_rating=3.0)
        ok, reason = _check_seller_rating(snap, FilterConfig())
        assert ok is False
        assert "buy" in reason

    def test_fails_no_ratings(self):
        snap = make_opportunity_snapshot(buy_seller_rating=None, sell_seller_rating=None)
        ok, reason = _check_seller_rating(snap, FilterConfig())
        assert ok is False
        assert "no seller rating" in reason


class TestCheckReviews:

    def test_passes_enough_reviews(self):
        snap = make_opportunity_snapshot(buy_reviews_count=30, sell_reviews_count=25)
        ok, _ = _check_reviews(snap, FilterConfig())
        assert ok is True

    def test_fails_few_reviews(self):
        snap = make_opportunity_snapshot(buy_reviews_count=10, sell_reviews_count=5)
        ok, _ = _check_reviews(snap, FilterConfig())
        assert ok is False


class TestCheckLiquidity:

    def test_passes_with_sales(self):
        snap = make_opportunity_snapshot(estimated_monthly_sales=10.0)
        ok, _ = _check_liquidity(snap, FilterConfig())
        assert ok is True

    def test_passes_with_reviews_fallback(self):
        snap = make_opportunity_snapshot(
            estimated_monthly_sales=1.0,
            buy_reviews_count=60, sell_reviews_count=50,
        )
        ok, _ = _check_liquidity(snap, FilterConfig())
        assert ok is True

    def test_fails_both_low(self):
        snap = make_opportunity_snapshot(
            estimated_monthly_sales=1.0,
            buy_reviews_count=10, sell_reviews_count=5,
        )
        ok, _ = _check_liquidity(snap, FilterConfig())
        assert ok is False


class TestCheckPriceConsistency:

    def test_passes_tight_spread(self):
        snap = make_opportunity_snapshot(price_spread_pct=0.05)
        ok, _ = _check_price_consistency(snap, FilterConfig())
        assert ok is True

    def test_fails_wide_spread(self):
        snap = make_opportunity_snapshot(price_spread_pct=0.25)
        ok, _ = _check_price_consistency(snap, FilterConfig())
        assert ok is False


class TestCheckTimeValidity:

    def test_passes_old_listing(self):
        snap = make_opportunity_snapshot(
            listing_created_at=datetime.now(timezone.utc) - timedelta(hours=10),
        )
        ok, _ = _check_time_validity(snap, FilterConfig())
        assert ok is True

    def test_fails_too_new(self):
        snap = make_opportunity_snapshot(
            listing_created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        ok, _ = _check_time_validity(snap, FilterConfig())
        assert ok is False

    def test_passes_no_timestamp(self):
        snap = make_opportunity_snapshot(listing_created_at=None)
        ok, _ = _check_time_validity(snap, FilterConfig())
        assert ok is True


class TestComputeLiquidityScore:

    def test_high_signals(self):
        score = compute_liquidity_score(monthly_sales=30.0, total_reviews=200)
        assert score == 100.0

    def test_zero_signals(self):
        score = compute_liquidity_score(monthly_sales=0, total_reviews=0)
        assert score == 0.0

    def test_partial_signals(self):
        score = compute_liquidity_score(monthly_sales=15.0, total_reviews=50)
        assert 0 < score < 100


class TestValidateOpportunity:

    def test_perfect_opportunity_passes(self):
        snap = make_opportunity_snapshot()
        result = validate_opportunity(snap)
        assert result.is_valid is True
        assert len(result.rejection_reasons) == 0
        assert all(result.passed_filters.values())

    def test_collects_all_rejection_reasons(self):
        snap = make_opportunity_snapshot(
            confidence_score=50.0,
            net_profit_usd=5.0,
            roi=0.03,
        )
        result = validate_opportunity(snap)
        assert result.is_valid is False
        assert len(result.rejection_reasons) >= 3  # confidence, profit, roi

    def test_no_short_circuit(self):
        """All filters run even if first one fails."""
        snap = make_opportunity_snapshot(
            confidence_score=50.0,
            net_profit_usd=5.0,
            roi=0.03,
            buy_seller_rating=2.0,
            buy_reviews_count=1,
            sell_reviews_count=1,
            estimated_monthly_sales=0.5,
            price_spread_pct=0.30,
        )
        result = validate_opportunity(snap)
        # All filters should have a result (no short-circuit)
        assert len(result.passed_filters) == 8

    def test_custom_config(self):
        snap = make_opportunity_snapshot(confidence_score=70.0, net_profit_usd=15.0)
        strict_cfg = FilterConfig(min_confidence_score=85.0, min_profit_usd=20.0)
        result = validate_opportunity(snap, config=strict_cfg)
        assert result.is_valid is False

        lenient_cfg = FilterConfig(min_confidence_score=60.0, min_profit_usd=10.0)
        result2 = validate_opportunity(snap, config=lenient_cfg)
        assert result2.passed_filters["confidence"] is True
        assert result2.passed_filters["profit"] is True

    def test_result_contains_scores(self):
        snap = make_opportunity_snapshot()
        result = validate_opportunity(snap)
        assert result.match_confidence == 90.0
        assert result.estimated_profit == 45.0
        assert result.liquidity_score > 0
