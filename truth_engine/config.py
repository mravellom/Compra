"""
Configuration for the Truth Engine — all thresholds in one place.

Every value has a sensible default. Override via environment or direct injection.
"""
import os
from dataclasses import dataclass, field


@dataclass
class TruthEngineConfig:
    """Master config for the entire truth/learning system."""

    # Execution tracker
    unsold_timeout_hours: float = float(os.getenv("UNSOLD_TIMEOUT_HOURS", "72"))

    # Time-to-sell model
    default_expected_days: float = 3.0
    fast_sale_days: float = 1.0
    slow_sale_days: float = 14.0
    time_decay_enabled: bool = True

    # Adaptive filters
    precision_target: float = float(os.getenv("PRECISION_TARGET", "0.70"))
    adaptation_window_days: int = int(os.getenv("ADAPTATION_WINDOW_DAYS", "30"))
    adjustment_step: float = 0.05
    min_samples_for_adaptation: int = int(os.getenv("MIN_SAMPLES_ADAPT", "10"))

    # Decision gate
    min_final_score: float = float(os.getenv("MIN_FINAL_SCORE", "0.15"))
    min_expected_value_usd: float = float(os.getenv("MIN_EXPECTED_VALUE", "10.0"))

    # Resolver feedback
    mismatch_penalty: float = 0.10
    max_penalty_per_pair: float = 0.50


@dataclass
class AdaptiveFilterBounds:
    """Hard bounds for adaptive filter thresholds — prevents runaway tightening/relaxation."""

    min_profit_bounds: tuple[float, float] = (5.0, 50.0)
    min_roi_bounds: tuple[float, float] = (0.05, 0.40)
    min_confidence_bounds: tuple[float, float] = (50.0, 95.0)
    min_reviews_bounds: tuple[int, int] = (10, 200)
    min_seller_rating_bounds: tuple[float, float] = (3.0, 4.8)


@dataclass
class TimeModelWeights:
    """Weights for time-to-sell estimation factors."""

    velocity_weight: float = 0.40
    reviews_weight: float = 0.25
    competitiveness_weight: float = 0.35
