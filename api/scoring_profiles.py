"""
Configurable Scoring Profiles — Strategy Pattern.

Different scoring weight presets for different use cases.
Each profile adjusts opportunity/risk/confidence weights
without changing the underlying normalization functions.

Profiles:
  - conservative: Prioritizes low risk, proven demand, high confidence
  - balanced:     Default v3 weights (current production)
  - aggressive:   Prioritizes high ROI, accepts thin margins and cross-border
  - volume:       Optimized for high-volume low-margin reselling

Risk Classification Matrix:
  Based on composite (opportunity_score, risk_score, confidence) → action label.
"""
import logging
from dataclasses import dataclass
from enum import Enum

from .scoring import ScoringInput, ScoringOutput, score as score_default
from .scoring import (
    _compute_factors,
    compute_opportunity_score,
    compute_risk_score,
    compute_confidence_score,
    OPP_WEIGHTS,
    RISK_WEIGHTS,
)

logger = logging.getLogger(__name__)


class ScoringProfile(Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"
    VOLUME = "volume"


class RiskClass(Enum):
    """Risk classification for automated decision making."""
    PRIME = "prime"           # Low risk, high confidence, high opportunity
    STANDARD = "standard"     # Moderate risk, decent opportunity
    SPECULATIVE = "speculative"  # High opportunity but also high risk
    AVOID = "avoid"           # Poor risk/reward ratio


@dataclass
class ScoringProfileConfig:
    """Weight overrides for a scoring profile."""
    name: str
    description: str

    # Opportunity weight overrides
    opp_weights: dict[str, float]

    # Risk weight overrides
    risk_weights: dict[str, float]

    # Thresholds for risk classification
    prime_min_opp: float = 65.0
    prime_max_risk: float = 35.0
    prime_min_conf: float = 55.0
    standard_min_opp: float = 40.0
    standard_max_risk: float = 55.0
    avoid_max_opp: float = 25.0


# ── Profile Definitions ──────────────────────────────────────

PROFILES: dict[str, ScoringProfileConfig] = {
    ScoringProfile.CONSERVATIVE.value: ScoringProfileConfig(
        name="conservative",
        description="Low risk, proven demand, high data confidence",
        opp_weights={
            "price_margin": 0.15,
            "marketplace_demand": 0.30,     # Heavy demand weight
            "historical_sales": 0.25,       # Proven sales track record
            "price_volatility": 0.10,
            "listing_volume": 0.10,
            "seller_reputation": 0.05,
            "price_stability": 0.05,
        },
        risk_weights={
            "volatility_risk": 0.30,        # Price stability matters most
            "competition_risk": 0.15,
            "liquidity_risk": 0.25,         # Must be easy to sell
            "margin_risk": 0.10,
            "cross_border_risk": 0.20,      # Avoid cross-border
        },
        prime_min_opp=70.0,
        prime_max_risk=30.0,
        prime_min_conf=60.0,
    ),
    ScoringProfile.BALANCED.value: ScoringProfileConfig(
        name="balanced",
        description="Default v3 weights — balanced risk/reward",
        opp_weights=dict(OPP_WEIGHTS),
        risk_weights=dict(RISK_WEIGHTS),
    ),
    ScoringProfile.AGGRESSIVE.value: ScoringProfileConfig(
        name="aggressive",
        description="High ROI focus, accepts more risk",
        opp_weights={
            "price_margin": 0.30,           # Profit is king
            "marketplace_demand": 0.20,
            "historical_sales": 0.15,
            "price_volatility": 0.05,       # Don't care about volatility
            "listing_volume": 0.10,
            "seller_reputation": 0.10,
            "price_stability": 0.10,
        },
        risk_weights={
            "volatility_risk": 0.15,
            "competition_risk": 0.25,
            "liquidity_risk": 0.20,
            "margin_risk": 0.25,            # Margin matters since we're aggressive
            "cross_border_risk": 0.15,      # Accept cross-border
        },
        prime_min_opp=55.0,                 # Lower bar for prime
        prime_max_risk=45.0,
        standard_min_opp=35.0,
    ),
    ScoringProfile.VOLUME.value: ScoringProfileConfig(
        name="volume",
        description="High-volume low-margin reselling",
        opp_weights={
            "price_margin": 0.10,           # Thin margins OK
            "marketplace_demand": 0.35,     # Must sell fast
            "historical_sales": 0.25,       # Proven velocity
            "price_volatility": 0.10,
            "listing_volume": 0.10,
            "seller_reputation": 0.05,
            "price_stability": 0.05,
        },
        risk_weights={
            "volatility_risk": 0.20,
            "competition_risk": 0.15,
            "liquidity_risk": 0.30,         # Liquidity is critical for volume
            "margin_risk": 0.20,
            "cross_border_risk": 0.15,
        },
        standard_min_opp=30.0,
        avoid_max_opp=15.0,
    ),
}


# ── Profile-Aware Scoring ─────────────────────────────────────

def score_with_profile(
    inp: ScoringInput,
    profile: str = "balanced",
) -> tuple[ScoringOutput, RiskClass]:
    """
    Score an opportunity using a specific profile, then classify risk.

    Returns (ScoringOutput, RiskClass).
    """
    config = PROFILES.get(profile, PROFILES["balanced"])

    # Use default scoring (it reads from module-level weights)
    # We apply profile adjustments as score modifiers
    result = score_default(inp)

    # Compute profile-adjusted opportunity score
    factors = _compute_factors(inp)
    adjusted_opp = sum(
        factors[name] * config.opp_weights.get(name, 0.0)
        for name in factors
    )

    # Apply same bonuses/penalties as default
    if factors["price_margin"] > 70 and factors["marketplace_demand"] > 60:
        adjusted_opp = min(100, adjusted_opp * 1.08)
    if factors["listing_volume"] < 15:
        adjusted_opp *= 0.90
    adjusted_opp -= inp.soft_penalty
    adjusted_opp = round(max(0, min(100, adjusted_opp)), 1)

    result.opportunity_score = adjusted_opp

    # Classify risk
    risk_class = classify_risk(result, config)

    return result, risk_class


def classify_risk(
    result: ScoringOutput,
    config: ScoringProfileConfig | None = None,
) -> RiskClass:
    """
    Risk Classification Matrix.

    Maps (opportunity_score, risk_score, confidence) to a risk class.

    ┌──────────────────┬───────────────┬──────────────┐
    │                  │ Risk ≤ 35     │ Risk > 35    │
    ├──────────────────┼───────────────┼──────────────┤
    │ Opp ≥ 65 + Conf │   PRIME       │ SPECULATIVE  │
    │ Opp ≥ 40        │   STANDARD    │ STANDARD     │
    │ Opp < 25        │   AVOID       │ AVOID        │
    └──────────────────┴───────────────┴──────────────┘
    """
    if config is None:
        config = PROFILES["balanced"]

    opp = result.opportunity_score
    risk = result.risk_score
    conf = result.confidence_score

    if opp >= config.prime_min_opp and risk <= config.prime_max_risk and conf >= config.prime_min_conf:
        return RiskClass.PRIME

    if opp <= config.avoid_max_opp:
        return RiskClass.AVOID

    if opp >= config.standard_min_opp and risk <= config.standard_max_risk:
        return RiskClass.STANDARD

    if opp >= config.standard_min_opp and risk > config.standard_max_risk:
        return RiskClass.SPECULATIVE

    return RiskClass.AVOID
