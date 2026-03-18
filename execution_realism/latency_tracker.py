"""
Latency Tracker — measures pipeline delay and penalizes stale decisions.

An opportunity detected 10 minutes ago has a higher chance of being gone.
Latency = execution_at - detected_at (full pipeline delay).
"""
import logging
from datetime import datetime, timezone

from .config import LatencyConfig
from .models import LatencyResult

logger = logging.getLogger(__name__)


class LatencyTracker:
    """Tracks and penalizes pipeline latency."""

    def __init__(self, config: LatencyConfig | None = None) -> None:
        self._cfg = config or LatencyConfig()

    def evaluate(
        self,
        detected_at: datetime,
        decision_at: datetime | None = None,
        execution_at: datetime | None = None,
    ) -> LatencyResult:
        """Compute latency and penalty.

        Args:
            detected_at: When the opportunity was first scraped.
            decision_at: When the decision gate approved it.
            execution_at: Now (when we're about to commit capital).
                          Defaults to utcnow().

        Returns:
            LatencyResult with penalty factor.
        """
        now = execution_at or datetime.now(timezone.utc)
        latency_seconds = (now - detected_at).total_seconds()
        latency_seconds = max(0.0, latency_seconds)

        # Hard reject: too stale
        if latency_seconds > self._cfg.max_latency_seconds:
            reason = (
                f"latency {latency_seconds:.0f}s exceeds "
                f"max {self._cfg.max_latency_seconds:.0f}s"
            )
            logger.info("Latency REJECTED: %s", reason)
            return LatencyResult(
                latency_seconds=round(latency_seconds, 1),
                is_acceptable=False,
                latency_penalty=1.0,
                detected_at=detected_at,
                decision_at=decision_at,
                execution_at=now,
                reason=reason,
            )

        # Penalty: starts after warning threshold
        penalty = 0.0
        if latency_seconds > self._cfg.warning_latency_seconds:
            excess_minutes = (
                (latency_seconds - self._cfg.warning_latency_seconds) / 60.0
            )
            penalty = min(1.0, excess_minutes * self._cfg.latency_penalty_per_minute)

        logger.debug(
            "Latency: %.0fs penalty=%.2f", latency_seconds, penalty,
        )

        return LatencyResult(
            latency_seconds=round(latency_seconds, 1),
            is_acceptable=True,
            latency_penalty=round(penalty, 4),
            detected_at=detected_at,
            decision_at=decision_at,
            execution_at=now,
        )
