"""
Resolver Feedback Store — penalizes brand/model/marketplace combinations that produce failed trades.

When a trade fails due to product mismatch:
→ penalize that brand + model + marketplace combination
→ future resolver confidence is reduced by accumulated penalty
"""
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from .config import TruthEngineConfig
from .models import ResolverPenalty

logger = logging.getLogger(__name__)


class ResolverFeedbackStore:
    """In-memory penalty store for resolver feedback.

    Key: (brand, model, marketplace) → penalty float.
    Thread-safe for single-process async; for multi-process, persist to DB.
    """

    def __init__(self, config: TruthEngineConfig | None = None) -> None:
        self._cfg = config or TruthEngineConfig()
        self._penalties: dict[tuple[str, str, str], ResolverPenalty] = {}

    def record_failure(
        self,
        brand: str,
        model: str,
        marketplace: str,
        reason: str = "",
    ) -> ResolverPenalty:
        """Record a trade failure for a brand/model/marketplace combination."""
        key = (brand.lower(), model.lower(), marketplace.lower())

        if key not in self._penalties:
            self._penalties[key] = ResolverPenalty(
                brand=brand.lower(),
                model=model.lower(),
                marketplace=marketplace.lower(),
            )

        entry = self._penalties[key]
        entry.failure_count += 1
        entry.last_failure_at = datetime.now(timezone.utc)
        entry.penalty = min(
            entry.penalty + self._cfg.mismatch_penalty,
            self._cfg.max_penalty_per_pair,
        )

        logger.info(
            "Resolver penalty: %s/%s/%s → %.2f (%d failures)",
            brand, model, marketplace, entry.penalty, entry.failure_count,
        )
        return entry

    def get_penalty(
        self,
        brand: str,
        model: str,
        marketplace: str,
    ) -> float:
        """Get accumulated penalty for a combination. Returns 0.0 if clean."""
        key = (brand.lower(), model.lower(), marketplace.lower())
        entry = self._penalties.get(key)
        return entry.penalty if entry else 0.0

    def get_adjusted_confidence(
        self,
        brand: str,
        model: str,
        marketplace: str,
        base_confidence: float,
    ) -> float:
        """Reduce resolver confidence by accumulated penalty."""
        penalty = self.get_penalty(brand, model, marketplace)
        adjusted = max(0.0, base_confidence - penalty)
        if penalty > 0:
            logger.debug(
                "Confidence adjusted: %s/%s/%s %.2f → %.2f (penalty=%.2f)",
                brand, model, marketplace, base_confidence, adjusted, penalty,
            )
        return adjusted

    def get_all_penalties(self) -> list[ResolverPenalty]:
        """Export all penalties (for API/logging)."""
        return list(self._penalties.values())

    def clear(self) -> None:
        """Reset all penalties."""
        self._penalties.clear()
