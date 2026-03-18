"""
Precision Metrics — aggregate performance measurement.

Computes system-level precision from truth engine data.
Exposes metrics for the /metrics endpoint.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from truth_engine.evaluator import TruthEvaluator
from truth_engine.models import PrecisionMetrics, TradeOutcome
from truth_engine.repository import TruthRepository

logger = logging.getLogger(__name__)


@dataclass
class SystemMetrics:
    """Full system metrics for the /metrics endpoint."""

    # Precision
    precision: float = 0.0
    total_detected: int = 0
    total_executed: int = 0
    total_successful: int = 0
    success_rate: float = 0.0

    # Profit accuracy
    avg_profit_error_pct: float = 0.0
    avg_actual_profit: float = 0.0
    avg_expected_profit: float = 0.0

    # ROI accuracy
    avg_roi_error_pct: float = 0.0

    # Time
    avg_time_to_sell_minutes: float = 0.0

    # Adaptive state
    current_thresholds: dict = None

    # Window
    window_days: int = 30

    def __post_init__(self):
        if self.current_thresholds is None:
            self.current_thresholds = {}


class PrecisionCalculator:
    """Computes and caches precision metrics."""

    def __init__(
        self,
        repository: TruthRepository,
        evaluator: TruthEvaluator | None = None,
    ) -> None:
        self._repo = repository
        self._eval = evaluator or TruthEvaluator()
        self._cached_metrics: Optional[PrecisionMetrics] = None
        self._cached_at: Optional[datetime] = None
        self._cache_ttl_seconds: int = 300  # 5 min cache

    async def compute(self, window_days: int = 30) -> PrecisionMetrics:
        """Compute precision metrics over window, with caching."""
        now = datetime.now(timezone.utc)

        if (
            self._cached_metrics is not None
            and self._cached_at is not None
            and (now - self._cached_at).total_seconds() < self._cache_ttl_seconds
            and self._cached_metrics.window_days == window_days
        ):
            return self._cached_metrics

        outcomes = await self._repo.get_executed_outcomes(window_days)
        total_detected = await self._repo.count_detected(window_days)

        metrics = self._eval.compute_precision_metrics(
            outcomes, total_detected, window_days,
        )

        self._cached_metrics = metrics
        self._cached_at = now
        return metrics

    async def get_system_metrics(
        self,
        window_days: int = 30,
        adaptive_thresholds: dict | None = None,
    ) -> SystemMetrics:
        """Full system metrics for API exposure."""
        pm = await self.compute(window_days)

        return SystemMetrics(
            precision=pm.precision,
            total_detected=pm.total_detected,
            total_executed=pm.total_executed,
            total_successful=pm.total_successful,
            success_rate=pm.precision,
            avg_profit_error_pct=pm.avg_profit_error_pct,
            avg_actual_profit=pm.avg_actual_profit,
            avg_expected_profit=pm.avg_expected_profit,
            avg_roi_error_pct=pm.avg_roi_error_pct,
            avg_time_to_sell_minutes=pm.avg_time_to_sell_minutes,
            current_thresholds=adaptive_thresholds or {},
            window_days=window_days,
        )

    def invalidate_cache(self) -> None:
        """Force recomputation on next call."""
        self._cached_metrics = None
        self._cached_at = None
