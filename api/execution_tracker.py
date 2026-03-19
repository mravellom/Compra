"""
Shadow Execution Tracker — measures predicted vs reality for every opportunity.

Unlike truth_engine/execution_tracker.py (which tracks actual executed trades),
this module tracks ALL emitted opportunities — whether executed or not — and
re-validates them after a configurable delay to measure:

  1. Profit drift: how much the real profit diverges from predicted
  2. Success rate: what % of opportunities remain profitable after delay
  3. Category decay: how fast prices move per marketplace/category
  4. False positive rate: % of opportunities that would have been unprofitable

This is a pure computation module with no DB dependency.  State lives in
memory (bounded deque) and is designed for real-time shadow monitoring.

Integration point: called from the opportunity scan cycle after scoring,
fed back into the adaptive filter engine and validator config.
"""
import logging
import math
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ── Configuration ────────────────────────────────────────────

@dataclass
class TrackerConfig:
    """All thresholds in one place."""

    revalidation_delay_seconds: float = 120.0   # seconds before re-check
    max_tracked: int = 5000                     # bounded history
    alert_success_rate: float = 0.70            # alert below this
    alert_drift_pct: float = 20.0               # alert above this
    alert_false_positive_rate: float = 0.30     # alert above this


# ── Data structures ──────────────────────────────────────────

@dataclass
class TrackedOpportunity:
    """One opportunity tracked from detection through re-validation."""

    opportunity_id: int
    product_id: int
    timestamp_detected: float                   # epoch seconds
    buy_price_predicted: float
    sell_price_predicted: float
    expected_profit: float
    expected_roi: float
    validation_confidence: float                # 0-1 from validator
    buy_marketplace: str = ""
    sell_marketplace: str = ""
    category: str = ""

    # Filled after re-validation
    revalidated: bool = False
    timestamp_revalidated: float = 0.0
    buy_price_real: float = 0.0
    sell_price_real: float = 0.0
    real_profit: float = 0.0
    real_roi: float = 0.0
    profit_drift: float = 0.0                  # real - predicted
    profit_drift_pct: float = 0.0              # % change
    still_profitable: bool = False
    delay_seconds: float = 0.0


@dataclass
class DriftStats:
    """Aggregated profit drift statistics."""

    sample_count: int = 0
    mean_drift: float = 0.0
    median_drift: float = 0.0
    worst_drift: float = 0.0                   # most negative
    std_drift: float = 0.0
    negative_flip_count: int = 0               # predicted profitable → real unprofitable
    negative_flip_rate: float = 0.0


@dataclass
class SuccessMetrics:
    """Aggregate success tracking."""

    total_tracked: int = 0
    total_revalidated: int = 0
    total_still_profitable: int = 0
    success_rate: float = 0.0                  # still_profitable / revalidated
    false_positive_rate: float = 0.0           # not profitable / revalidated
    avg_predicted_profit: float = 0.0
    avg_real_profit: float = 0.0
    avg_delay_seconds: float = 0.0


@dataclass
class CategoryDecay:
    """Per-category price decay measurement."""

    category: str
    sample_count: int = 0
    avg_drift_pct: float = 0.0
    decay_factor_per_minute: float = 0.0       # average price change/minute


@dataclass
class AlertStatus:
    """System health alerts."""

    success_rate_alert: bool = False
    drift_alert: bool = False
    false_positive_alert: bool = False
    messages: list[str] = field(default_factory=list)


# ── Core tracker ─────────────────────────────────────────────

class ShadowExecutionTracker:
    """Tracks every emitted opportunity and measures prediction accuracy.

    Usage:
        tracker = ShadowExecutionTracker()

        # When opportunity detected:
        tracker.record(opportunity_id=42, ...)

        # After delay (e.g. 120s), re-validate with fresh prices:
        tracker.revalidate(opportunity_id=42, buy_price_real=..., sell_price_real=...)

        # Get aggregate metrics:
        metrics = tracker.compute_success_metrics()
        drift = tracker.compute_drift_stats()
        alerts = tracker.check_alerts()
    """

    def __init__(self, config: TrackerConfig | None = None) -> None:
        self._cfg = config or TrackerConfig()
        self._tracked: deque[TrackedOpportunity] = deque(
            maxlen=self._cfg.max_tracked,
        )
        self._by_id: dict[int, TrackedOpportunity] = {}

    @property
    def tracked_count(self) -> int:
        return len(self._tracked)

    @property
    def revalidated_count(self) -> int:
        return sum(1 for t in self._tracked if t.revalidated)

    def record(
        self,
        opportunity_id: int,
        product_id: int,
        buy_price_predicted: float,
        sell_price_predicted: float,
        expected_profit: float,
        expected_roi: float,
        validation_confidence: float,
        buy_marketplace: str = "",
        sell_marketplace: str = "",
        category: str = "",
    ) -> TrackedOpportunity:
        """Record a newly detected opportunity for shadow tracking."""
        entry = TrackedOpportunity(
            opportunity_id=opportunity_id,
            product_id=product_id,
            timestamp_detected=time.time(),
            buy_price_predicted=buy_price_predicted,
            sell_price_predicted=sell_price_predicted,
            expected_profit=expected_profit,
            expected_roi=expected_roi,
            validation_confidence=validation_confidence,
            buy_marketplace=buy_marketplace,
            sell_marketplace=sell_marketplace,
            category=category,
        )
        self._tracked.append(entry)
        self._by_id[opportunity_id] = entry

        logger.debug(
            "Shadow tracking opp=%d product=%d profit=$%.2f conf=%.2f",
            opportunity_id, product_id, expected_profit, validation_confidence,
        )
        return entry

    def revalidate(
        self,
        opportunity_id: int,
        buy_price_real: float,
        sell_price_real: float,
        real_fees: float = 0.0,
    ) -> Optional[TrackedOpportunity]:
        """Re-validate an opportunity with fresh prices after delay.

        Args:
            opportunity_id: ID of the tracked opportunity.
            buy_price_real: Current buy price (re-fetched).
            sell_price_real: Current realistic sell price (re-fetched).
            real_fees: Fees at real prices (0 = use profit delta only).
        """
        entry = self._by_id.get(opportunity_id)
        if entry is None:
            logger.debug("No shadow entry for opp=%d", opportunity_id)
            return None

        if entry.revalidated:
            return entry  # Already done

        now = time.time()
        entry.timestamp_revalidated = now
        entry.delay_seconds = now - entry.timestamp_detected
        entry.buy_price_real = buy_price_real
        entry.sell_price_real = sell_price_real

        if real_fees > 0:
            entry.real_profit = sell_price_real - buy_price_real - real_fees
        else:
            # Estimate real profit from price deltas applied to original profit
            buy_delta = buy_price_real - entry.buy_price_predicted
            sell_delta = sell_price_real - entry.sell_price_predicted
            entry.real_profit = entry.expected_profit + sell_delta - buy_delta

        entry.real_roi = (
            entry.real_profit / buy_price_real
            if buy_price_real > 0 else 0.0
        )

        # Drift
        entry.profit_drift = entry.real_profit - entry.expected_profit
        if abs(entry.expected_profit) > 0:
            entry.profit_drift_pct = (
                entry.profit_drift / abs(entry.expected_profit)
            ) * 100.0
        else:
            entry.profit_drift_pct = 0.0

        entry.still_profitable = entry.real_profit > 0
        entry.revalidated = True

        logger.debug(
            "Shadow revalidated opp=%d: predicted=$%.2f real=$%.2f drift=$%.2f (%.1f%%) delay=%.0fs",
            opportunity_id, entry.expected_profit, entry.real_profit,
            entry.profit_drift, entry.profit_drift_pct, entry.delay_seconds,
        )
        return entry

    # ── Aggregation ──────────────────────────────────────────

    def _revalidated_entries(self) -> list[TrackedOpportunity]:
        return [t for t in self._tracked if t.revalidated]

    def compute_drift_stats(self) -> DriftStats:
        """Compute profit drift distribution from revalidated opportunities."""
        entries = self._revalidated_entries()
        if not entries:
            return DriftStats()

        drifts = [e.profit_drift for e in entries]
        neg_flips = sum(
            1 for e in entries
            if e.expected_profit > 0 and not e.still_profitable
        )

        return DriftStats(
            sample_count=len(entries),
            mean_drift=round(statistics.mean(drifts), 2),
            median_drift=round(statistics.median(drifts), 2),
            worst_drift=round(min(drifts), 2),
            std_drift=round(statistics.stdev(drifts), 2) if len(drifts) >= 2 else 0.0,
            negative_flip_count=neg_flips,
            negative_flip_rate=round(neg_flips / len(entries), 4),
        )

    def compute_success_metrics(self) -> SuccessMetrics:
        """Compute aggregate success rate metrics."""
        entries = self._revalidated_entries()
        total_tracked = len(self._tracked)

        if not entries:
            return SuccessMetrics(total_tracked=total_tracked)

        still_profitable = sum(1 for e in entries if e.still_profitable)
        not_profitable = len(entries) - still_profitable

        return SuccessMetrics(
            total_tracked=total_tracked,
            total_revalidated=len(entries),
            total_still_profitable=still_profitable,
            success_rate=round(still_profitable / len(entries), 4),
            false_positive_rate=round(not_profitable / len(entries), 4),
            avg_predicted_profit=round(
                statistics.mean(e.expected_profit for e in entries), 2,
            ),
            avg_real_profit=round(
                statistics.mean(e.real_profit for e in entries), 2,
            ),
            avg_delay_seconds=round(
                statistics.mean(e.delay_seconds for e in entries), 1,
            ),
        )

    def compute_category_decay(self) -> list[CategoryDecay]:
        """Compute per-category price decay rates."""
        entries = self._revalidated_entries()
        if not entries:
            return []

        by_cat: dict[str, list[TrackedOpportunity]] = {}
        for e in entries:
            cat = e.category or "unknown"
            by_cat.setdefault(cat, []).append(e)

        result: list[CategoryDecay] = []
        for cat, items in sorted(by_cat.items()):
            drifts = [i.profit_drift_pct for i in items]
            delays = [i.delay_seconds for i in items if i.delay_seconds > 0]

            avg_drift = statistics.mean(drifts) if drifts else 0.0

            # Decay per minute: drift_pct / delay_minutes
            decay_per_min = 0.0
            if delays:
                avg_delay_min = statistics.mean(delays) / 60.0
                if avg_delay_min > 0:
                    decay_per_min = abs(avg_drift) / avg_delay_min

            result.append(CategoryDecay(
                category=cat,
                sample_count=len(items),
                avg_drift_pct=round(avg_drift, 2),
                decay_factor_per_minute=round(decay_per_min, 4),
            ))

        return result

    # ── Alerts ───────────────────────────────────────────────

    def check_alerts(self) -> AlertStatus:
        """Check if any metrics exceed alert thresholds."""
        metrics = self.compute_success_metrics()
        drift = self.compute_drift_stats()
        cfg = self._cfg
        alerts = AlertStatus()

        if metrics.total_revalidated < 5:
            return alerts  # Not enough data

        if metrics.success_rate < cfg.alert_success_rate:
            alerts.success_rate_alert = True
            alerts.messages.append(
                f"success_rate {metrics.success_rate:.1%} < {cfg.alert_success_rate:.0%}"
            )

        if abs(drift.mean_drift) > 0 and abs(
            drift.mean_drift / max(abs(metrics.avg_predicted_profit), 1)
        ) * 100 > cfg.alert_drift_pct:
            alerts.drift_alert = True
            alerts.messages.append(
                f"mean_drift ${drift.mean_drift:.2f} exceeds {cfg.alert_drift_pct:.0f}% threshold"
            )

        if metrics.false_positive_rate > cfg.alert_false_positive_rate:
            alerts.false_positive_alert = True
            alerts.messages.append(
                f"false_positive_rate {metrics.false_positive_rate:.1%} > "
                f"{cfg.alert_false_positive_rate:.0%}"
            )

        if alerts.messages:
            logger.warning(
                "Shadow execution alerts: %s", "; ".join(alerts.messages),
            )

        return alerts

    # ── Feedback ─────────────────────────────────────────────

    def get_feedback_adjustments(self) -> dict:
        """Compute adjustments to feed back into the system.

        Returns a dict with recommended adjustments for:
        - profit_buffer_pct: how much to inflate the profit safety margin
        - decay_rate_per_hour: observed price decay rate
        - confidence_threshold_delta: whether to tighten or relax
        """
        metrics = self.compute_success_metrics()
        drift = self.compute_drift_stats()

        if metrics.total_revalidated < 10:
            return {
                "has_data": False,
                "profit_buffer_pct": 0.0,
                "decay_rate_per_hour": 0.0,
                "confidence_threshold_delta": 0.0,
            }

        # Profit buffer: absorb the mean negative drift
        profit_buffer = 0.0
        if drift.mean_drift < 0 and metrics.avg_predicted_profit > 0:
            profit_buffer = abs(drift.mean_drift) / metrics.avg_predicted_profit

        # Decay rate: average category decay scaled to per-hour
        categories = self.compute_category_decay()
        avg_decay_per_min = 0.0
        if categories:
            avg_decay_per_min = statistics.mean(
                c.decay_factor_per_minute for c in categories
            )
        decay_per_hour = avg_decay_per_min * 60.0

        # Confidence delta: tighten if false positive rate is high
        conf_delta = 0.0
        if metrics.false_positive_rate > 0.30:
            conf_delta = 0.05  # tighten by 5 points
        elif metrics.false_positive_rate < 0.10:
            conf_delta = -0.02  # relax slightly

        return {
            "has_data": True,
            "profit_buffer_pct": round(profit_buffer, 4),
            "decay_rate_per_hour": round(decay_per_hour, 6),
            "confidence_threshold_delta": round(conf_delta, 4),
            "success_rate": metrics.success_rate,
            "false_positive_rate": metrics.false_positive_rate,
            "mean_drift": drift.mean_drift,
            "sample_count": metrics.total_revalidated,
        }

    def clear(self) -> None:
        """Reset all state (for testing)."""
        self._tracked.clear()
        self._by_id.clear()

    def get_pending_revalidation(self) -> list[TrackedOpportunity]:
        """Get opportunities ready for re-validation (delay exceeded, not yet done)."""
        now = time.time()
        cutoff = now - self._cfg.revalidation_delay_seconds
        return [
            t for t in self._tracked
            if not t.revalidated and t.timestamp_detected <= cutoff
        ]
