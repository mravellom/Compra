"""
Unit Tests — Scoring Profiles & Risk Classification.

Tests:
  - Profile weight configurations (conservative, balanced, aggressive, volume)
  - Profile-adjusted scoring
  - Risk classification matrix (PRIME, STANDARD, SPECULATIVE, AVOID)
"""
import pytest

from api.scoring import ScoringInput, ScoringOutput
from api.scoring_profiles import (
    ScoringProfile,
    ScoringProfileConfig,
    RiskClass,
    PROFILES,
    score_with_profile,
    classify_risk,
)

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fixtures.conftest import make_scoring_input


# ═══════════════════════════════════════════════════════════════
# 1. Profile Definitions
# ═══════════════════════════════════════════════════════════════


class TestProfileDefinitions:

    def test_all_profiles_exist(self):
        for profile in ScoringProfile:
            assert profile.value in PROFILES

    def test_all_profile_weights_sum_to_one(self):
        for name, config in PROFILES.items():
            total = sum(config.opp_weights.values())
            assert total == pytest.approx(1.0, abs=0.01), f"{name} opp_weights sum: {total}"
            risk_total = sum(config.risk_weights.values())
            assert risk_total == pytest.approx(1.0, abs=0.01), f"{name} risk_weights sum: {risk_total}"

    def test_balanced_matches_default_weights(self):
        from api.scoring import OPP_WEIGHTS, RISK_WEIGHTS
        balanced = PROFILES["balanced"]
        for key in OPP_WEIGHTS:
            assert balanced.opp_weights[key] == pytest.approx(OPP_WEIGHTS[key])

    def test_conservative_prioritizes_demand(self):
        conservative = PROFILES["conservative"]
        assert conservative.opp_weights["marketplace_demand"] >= 0.30

    def test_aggressive_prioritizes_margin(self):
        aggressive = PROFILES["aggressive"]
        assert aggressive.opp_weights["price_margin"] >= 0.30

    def test_volume_prioritizes_demand_and_sales(self):
        volume = PROFILES["volume"]
        demand_weight = volume.opp_weights["marketplace_demand"]
        sales_weight = volume.opp_weights["historical_sales"]
        assert demand_weight + sales_weight >= 0.55


# ═══════════════════════════════════════════════════════════════
# 2. Profile-Adjusted Scoring
# ═══════════════════════════════════════════════════════════════


class TestScoreWithProfile:

    def test_balanced_returns_output(self):
        inp = make_scoring_input()
        result, risk_class = score_with_profile(inp, "balanced")
        assert isinstance(result, ScoringOutput)
        assert isinstance(risk_class, RiskClass)

    def test_different_profiles_different_scores(self):
        inp = make_scoring_input(
            margin=0.40,
            roi=0.50,
            total_sales_count=200,
            total_reviews_count=100,
        )
        results = {}
        for profile in ["conservative", "balanced", "aggressive", "volume"]:
            output, _ = score_with_profile(inp, profile)
            results[profile] = output.opportunity_score

        # At minimum, not all profiles should produce the exact same score
        unique_scores = set(round(s, 0) for s in results.values())
        assert len(unique_scores) >= 2

    def test_aggressive_higher_for_high_margin(self):
        """Aggressive profile should score high-margin opportunities higher."""
        inp = make_scoring_input(
            margin=0.45, roi=0.80, net_profit_usd=80.0,
            total_sales_count=5, total_reviews_count=10,
        )
        agg_result, _ = score_with_profile(inp, "aggressive")
        cons_result, _ = score_with_profile(inp, "conservative")
        assert agg_result.opportunity_score >= cons_result.opportunity_score

    def test_conservative_higher_for_proven_demand(self):
        """Conservative should prefer opportunities with high proven demand."""
        inp = make_scoring_input(
            margin=0.08, roi=0.10, net_profit_usd=10.0,
            total_sales_count=500, total_reviews_count=300,
            estimated_daily_sales=10.0, market_depth_score=90.0,
        )
        cons_result, _ = score_with_profile(inp, "conservative")
        # With proven demand, conservative should still score reasonably
        assert cons_result.opportunity_score > 20

    def test_unknown_profile_falls_back_to_balanced(self):
        inp = make_scoring_input()
        result, _ = score_with_profile(inp, "nonexistent")
        balanced_result, _ = score_with_profile(inp, "balanced")
        assert result.opportunity_score == balanced_result.opportunity_score


# ═══════════════════════════════════════════════════════════════
# 3. Risk Classification Matrix
# ═══════════════════════════════════════════════════════════════


class TestRiskClassification:

    def _output(self, opp=60, risk=30, conf=65):
        return ScoringOutput(
            opportunity_score=opp,
            risk_score=risk,
            confidence_score=conf,
            confidence_level="high" if conf >= 60 else "medium",
            factors={},
        )

    def test_prime_classification(self):
        """High opp, low risk, high confidence → PRIME."""
        result = self._output(opp=75, risk=25, conf=65)
        assert classify_risk(result) == RiskClass.PRIME

    def test_standard_classification(self):
        """Moderate opp, moderate risk → STANDARD."""
        result = self._output(opp=50, risk=40, conf=50)
        assert classify_risk(result) == RiskClass.STANDARD

    def test_speculative_classification(self):
        """Good opp but high risk → SPECULATIVE."""
        result = self._output(opp=55, risk=60, conf=50)
        assert classify_risk(result) == RiskClass.SPECULATIVE

    def test_avoid_low_opportunity(self):
        """Very low opportunity score → AVOID."""
        result = self._output(opp=15, risk=30, conf=50)
        assert classify_risk(result) == RiskClass.AVOID

    def test_profile_adjusted_thresholds(self):
        """Conservative has tighter thresholds for PRIME."""
        conservative = PROFILES["conservative"]
        result = self._output(opp=68, risk=32, conf=58)
        # Conservative: prime_min_opp=70 → not PRIME
        assert classify_risk(result, conservative) != RiskClass.PRIME

    def test_aggressive_relaxed_thresholds(self):
        """Aggressive has relaxed thresholds for PRIME."""
        aggressive = PROFILES["aggressive"]
        result = self._output(opp=58, risk=40, conf=60)
        # Aggressive: prime_min_opp=55, prime_max_risk=45, prime_min_conf=55
        assert classify_risk(result, aggressive) == RiskClass.PRIME
