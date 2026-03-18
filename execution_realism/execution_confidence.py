"""
Execution Confidence — composite score from all realism signals.

Combines:
- Price stability (from validation pass/fail and delta)
- Stock availability (including confidence adjustments)
- Latency penalty
- Competition score

If composite confidence falls below threshold → final REJECT.
"""
import logging

from .config import ExecutionRealismConfig
from .models import (
    CompetitionResult,
    ExecutionConfidenceResult,
    LatencyResult,
    PriceValidationResult,
    StockValidationResult,
)

logger = logging.getLogger(__name__)


class ExecutionConfidenceCalculator:
    """Computes composite execution confidence from realism signals."""

    def __init__(self, config: ExecutionRealismConfig | None = None) -> None:
        self._cfg = config or ExecutionRealismConfig()

    def compute(
        self,
        price_check: PriceValidationResult,
        stock_check: StockValidationResult,
        latency: LatencyResult,
        competition: CompetitionResult,
    ) -> ExecutionConfidenceResult:
        """Combine all realism signals into a single confidence score.

        Formula:
            confidence = price_factor * stock_factor * (1 - latency_penalty) * (1 - competition_score)

        Each factor is 0-1. The product naturally penalizes any weak link.
        """
        # Price stability: 1.0 if valid, scales down by deviation magnitude
        if price_check.is_valid:
            price_factor = max(0.5, 1.0 - abs(price_check.price_delta_pct) * 5.0)
        else:
            price_factor = 0.0

        # Stock: use the confidence_adjustment from StockValidator
        stock_factor = stock_check.confidence_adjustment if stock_check.available else 0.0

        # Latency: invert the penalty
        latency_factor = max(0.0, 1.0 - latency.latency_penalty) if latency.is_acceptable else 0.0

        # Competition: higher competition → lower confidence
        competition_factor = max(0.0, 1.0 - competition.competition_score)

        # Composite
        confidence = price_factor * stock_factor * latency_factor * competition_factor

        is_acceptable = confidence >= self._cfg.min_execution_confidence

        components = {
            "price_factor": round(price_factor, 4),
            "stock_factor": round(stock_factor, 4),
            "latency_factor": round(latency_factor, 4),
            "competition_factor": round(competition_factor, 4),
        }

        logger.debug(
            "Execution confidence: %.3f (price=%.2f stock=%.2f lat=%.2f comp=%.2f) accept=%s",
            confidence, price_factor, stock_factor, latency_factor, competition_factor,
            is_acceptable,
        )

        return ExecutionConfidenceResult(
            execution_confidence=round(confidence, 4),
            is_acceptable=is_acceptable,
            components=components,
        )
