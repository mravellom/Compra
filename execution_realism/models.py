"""
Domain models for the Execution Realism Layer.

All result types are frozen dataclasses — immutable, deterministic, auditable.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class PriceValidationResult:
    """Output of real-time price re-check."""

    is_valid: bool
    expected_buy_price: float
    current_buy_price: float
    price_delta_pct: float            # (current - expected) / expected
    original_profit: float
    updated_profit: float
    reason: str = ""


@dataclass(frozen=True)
class StockValidationResult:
    """Output of stock availability check."""

    available: bool
    stock_level: Optional[int]        # None = unknown
    confidence_adjustment: float      # multiplier (1.0 = no change, <1.0 = reduced)
    reason: str = ""


@dataclass(frozen=True)
class LatencyResult:
    """Output of latency tracking."""

    latency_seconds: float
    is_acceptable: bool
    latency_penalty: float            # 0-1 penalty factor
    detected_at: Optional[datetime] = None
    decision_at: Optional[datetime] = None
    execution_at: Optional[datetime] = None
    reason: str = ""


@dataclass(frozen=True)
class SlippageResult:
    """Output of slippage estimation."""

    estimated_slippage_pct: float
    slipped_buy_price: float          # expected * (1 + slippage)
    profit_after_slippage: float
    is_viable: bool
    reason: str = ""


@dataclass(frozen=True)
class CompetitionResult:
    """Output of competition analysis."""

    competition_score: float          # 0-1 (0 = no competition, 1 = saturated)
    adjusted_p_sale: float            # probability_of_sale after competition
    signals: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionConfidenceResult:
    """Composite execution confidence."""

    execution_confidence: float       # 0-1 composite
    is_acceptable: bool
    components: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RealismCheckResult:
    """Final aggregated result from the Execution Realism Layer."""

    approved: bool
    rejection_reasons: list[str] = field(default_factory=list)

    # Sub-results for full observability
    price_check: Optional[PriceValidationResult] = None
    stock_check: Optional[StockValidationResult] = None
    slippage: Optional[SlippageResult] = None
    latency: Optional[LatencyResult] = None
    competition: Optional[CompetitionResult] = None
    execution_confidence: Optional[ExecutionConfidenceResult] = None

    # Final adjusted values
    final_buy_price: float = 0.0
    final_profit: float = 0.0
    final_p_sale: float = 0.0
