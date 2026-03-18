"""
Adaptive Filters — dynamic threshold adjustment based on real precision.

If precision < target → tighten filters (fewer false positives).
If precision > target → relax filters slightly (capture more opportunities).

Never exceeds hard bounds. Requires minimum sample size before adapting.
"""
import logging
from dataclasses import dataclass

from .config import AdaptiveFilterBounds, TruthEngineConfig
from .models import PrecisionMetrics

logger = logging.getLogger(__name__)


@dataclass
class AdaptiveFilterState:
    """Current adaptive filter thresholds — replaces static FilterConfig."""

    min_profit_usd: float = 20.0
    min_roi: float = 0.15
    min_confidence_score: float = 85.0
    min_reviews_count: int = 50
    min_seller_rating: float = 4.2

    # Track how many times we've adapted
    tighten_count: int = 0
    relax_count: int = 0
    last_precision: float = 0.0


class AdaptiveFilterEngine:
    """Adjusts filter thresholds based on observed precision."""

    def __init__(
        self,
        config: TruthEngineConfig | None = None,
        bounds: AdaptiveFilterBounds | None = None,
        state: AdaptiveFilterState | None = None,
    ) -> None:
        self._cfg = config or TruthEngineConfig()
        self._bounds = bounds or AdaptiveFilterBounds()
        self.state = state or AdaptiveFilterState()

    def adapt(self, metrics: PrecisionMetrics) -> AdaptiveFilterState:
        """Adjust thresholds based on latest precision metrics.

        Rules:
        - Need min_samples_for_adaptation executed trades to adapt.
        - precision < target → tighten (increase thresholds by adjustment_step).
        - precision > target → relax (decrease thresholds by adjustment_step * 0.5).
        - Never exceed hard bounds.
        """
        self.state.last_precision = metrics.precision

        if metrics.total_executed < self._cfg.min_samples_for_adaptation:
            logger.info(
                "Skipping adaptation: only %d executed (need %d)",
                metrics.total_executed, self._cfg.min_samples_for_adaptation,
            )
            return self.state

        step = self._cfg.adjustment_step
        target = self._cfg.precision_target

        if metrics.precision < target:
            self._tighten(step)
        elif metrics.precision > target:
            # Relax more gently than we tighten (asymmetric)
            self._relax(step * 0.5)

        logger.info(
            "Adaptive filters: precision=%.2f target=%.2f → "
            "profit=$%.1f roi=%.0f%% conf=%.0f reviews=%d rating=%.1f "
            "(tighten=%d relax=%d)",
            metrics.precision, target,
            self.state.min_profit_usd, self.state.min_roi * 100,
            self.state.min_confidence_score, self.state.min_reviews_count,
            self.state.min_seller_rating,
            self.state.tighten_count, self.state.relax_count,
        )

        return self.state

    def _tighten(self, step: float) -> None:
        """Increase thresholds to reduce false positives."""
        b = self._bounds

        self.state.min_profit_usd = min(
            self.state.min_profit_usd + step * 40,   # $2 per step
            b.min_profit_bounds[1],
        )
        self.state.min_roi = min(
            self.state.min_roi + step,
            b.min_roi_bounds[1],
        )
        self.state.min_confidence_score = min(
            self.state.min_confidence_score + step * 20,  # 1 point per step
            b.min_confidence_bounds[1],
        )
        self.state.min_reviews_count = min(
            int(self.state.min_reviews_count + step * 200),  # 10 per step
            b.min_reviews_bounds[1],
        )
        self.state.min_seller_rating = min(
            self.state.min_seller_rating + step * 2,  # 0.1 per step
            b.min_seller_rating_bounds[1],
        )
        self.state.tighten_count += 1

    def _relax(self, step: float) -> None:
        """Decrease thresholds to capture more opportunities."""
        b = self._bounds

        self.state.min_profit_usd = max(
            self.state.min_profit_usd - step * 40,
            b.min_profit_bounds[0],
        )
        self.state.min_roi = max(
            self.state.min_roi - step,
            b.min_roi_bounds[0],
        )
        self.state.min_confidence_score = max(
            self.state.min_confidence_score - step * 20,
            b.min_confidence_bounds[0],
        )
        self.state.min_reviews_count = max(
            int(self.state.min_reviews_count - step * 200),
            b.min_reviews_bounds[0],
        )
        self.state.min_seller_rating = max(
            self.state.min_seller_rating - step * 2,
            b.min_seller_rating_bounds[0],
        )
        self.state.relax_count += 1

    def get_current_thresholds(self) -> dict[str, float]:
        """Export current thresholds as a dict (for logging/API)."""
        return {
            "min_profit_usd": self.state.min_profit_usd,
            "min_roi": self.state.min_roi,
            "min_confidence_score": self.state.min_confidence_score,
            "min_reviews_count": self.state.min_reviews_count,
            "min_seller_rating": self.state.min_seller_rating,
            "last_precision": self.state.last_precision,
            "tighten_count": self.state.tighten_count,
            "relax_count": self.state.relax_count,
        }
