"""
Domain models for the Truth Engine.

Pure data classes — no DB dependency. Serializable, testable, immutable-ish.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class TradeOutcome:
    """Ground truth for a single trade attempt."""

    opportunity_id: int
    detected_at: datetime
    executed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Predicted values (from opportunity engine)
    buy_price_predicted: float = 0.0
    sell_price_predicted: float = 0.0
    estimated_profit: float = 0.0
    expected_roi: float = 0.0

    # Actual values (from execution)
    buy_price_actual: float = 0.0
    sell_price_actual: float = 0.0
    actual_profit: float = 0.0
    actual_roi: float = 0.0

    # Time tracking
    time_to_sell_minutes: Optional[float] = None

    # Status
    sold: bool = False
    cancelled: bool = False
    failure_reason: Optional[str] = None

    # Metadata
    buy_marketplace: str = ""
    sell_marketplace: str = ""
    master_product_id: Optional[int] = None

    # DB identity
    id: Optional[int] = None


@dataclass
class OutcomeEvaluation:
    """Result of comparing predicted vs actual for one trade."""

    opportunity_id: int
    profit_error_pct: float          # (actual - estimated) / estimated * 100
    roi_error_pct: float             # (actual_roi - expected_roi) / expected_roi * 100
    success: bool                    # actual_profit > 0 AND sold == True
    time_to_sell_minutes: Optional[float] = None
    actual_profit: float = 0.0
    estimated_profit: float = 0.0


@dataclass
class PrecisionMetrics:
    """Aggregate precision metrics over a time window."""

    total_detected: int = 0
    total_executed: int = 0
    total_successful: int = 0
    total_failed: int = 0
    precision: float = 0.0           # successful / executed
    avg_profit_error_pct: float = 0.0
    avg_roi_error_pct: float = 0.0
    avg_time_to_sell_minutes: float = 0.0
    avg_actual_profit: float = 0.0
    avg_expected_profit: float = 0.0
    window_days: int = 30


@dataclass
class ResolverPenalty:
    """Accumulated penalty for a brand/model/marketplace combination."""

    brand: str
    model: str
    marketplace: str
    penalty: float = 0.0
    failure_count: int = 0
    last_failure_at: Optional[datetime] = None


@dataclass
class DecisionInput:
    """All inputs needed for the final execution decision."""

    opportunity_id: int
    expected_value: float
    confidence_score: float          # 0-100
    liquidity_score: float           # 0-100
    risk_score: float                # 0-100
    probability_of_sale: float       # 0-1
    adjusted_profit: float
    estimated_time_to_sell_days: float


@dataclass
class DecisionResult:
    """Output of the decision gate."""

    opportunity_id: int
    final_score: float
    execute: bool
    rejection_reasons: list[str] = field(default_factory=list)
    inputs: Optional[DecisionInput] = None
