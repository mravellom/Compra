"""
TruthEvaluator — compares predicted vs actual trade outcomes.

Core of the feedback loop: measures how accurate our predictions are.
"""
import logging
import statistics
from typing import Optional

from .models import OutcomeEvaluation, PrecisionMetrics, TradeOutcome

logger = logging.getLogger(__name__)


class TruthEvaluator:
    """Evaluates trade outcomes against predictions."""

    def evaluate_outcome(self, outcome: TradeOutcome) -> OutcomeEvaluation:
        """Compare a single outcome's predicted vs actual values.

        Returns an evaluation with:
        - profit_error_pct: how far off the profit prediction was
        - roi_error_pct: how far off the ROI prediction was
        - success: True if actual_profit > 0 AND sold == True
        """
        # Profit error %
        if outcome.estimated_profit != 0:
            profit_error = (
                (outcome.actual_profit - outcome.estimated_profit)
                / abs(outcome.estimated_profit)
            ) * 100.0
        else:
            profit_error = 0.0 if outcome.actual_profit == 0 else 100.0

        # ROI error %
        if outcome.expected_roi != 0:
            roi_error = (
                (outcome.actual_roi - outcome.expected_roi)
                / abs(outcome.expected_roi)
            ) * 100.0
        else:
            roi_error = 0.0 if outcome.actual_roi == 0 else 100.0

        success = outcome.actual_profit > 0 and outcome.sold

        return OutcomeEvaluation(
            opportunity_id=outcome.opportunity_id,
            profit_error_pct=round(profit_error, 2),
            roi_error_pct=round(roi_error, 2),
            success=success,
            time_to_sell_minutes=outcome.time_to_sell_minutes,
            actual_profit=outcome.actual_profit,
            estimated_profit=outcome.estimated_profit,
        )

    def compute_precision_metrics(
        self,
        outcomes: list[TradeOutcome],
        total_detected: int = 0,
        window_days: int = 30,
    ) -> PrecisionMetrics:
        """Compute aggregate precision metrics from a list of outcomes.

        Args:
            outcomes: Executed trade outcomes (not all detected — only those attempted).
            total_detected: Total opportunities detected in the period (for context).
            window_days: Reporting window.
        """
        if not outcomes:
            return PrecisionMetrics(
                total_detected=total_detected,
                window_days=window_days,
            )

        evaluations = [self.evaluate_outcome(o) for o in outcomes]

        total_executed = len(outcomes)
        total_successful = sum(1 for e in evaluations if e.success)
        total_failed = total_executed - total_successful

        precision = total_successful / total_executed if total_executed > 0 else 0.0

        profit_errors = [e.profit_error_pct for e in evaluations]
        roi_errors = [e.roi_error_pct for e in evaluations]
        sell_times = [
            e.time_to_sell_minutes for e in evaluations
            if e.time_to_sell_minutes is not None
        ]

        avg_profit_error = statistics.mean(profit_errors) if profit_errors else 0.0
        avg_roi_error = statistics.mean(roi_errors) if roi_errors else 0.0
        avg_time = statistics.mean(sell_times) if sell_times else 0.0

        actual_profits = [e.actual_profit for e in evaluations]
        expected_profits = [e.estimated_profit for e in evaluations]

        metrics = PrecisionMetrics(
            total_detected=total_detected,
            total_executed=total_executed,
            total_successful=total_successful,
            total_failed=total_failed,
            precision=round(precision, 4),
            avg_profit_error_pct=round(avg_profit_error, 2),
            avg_roi_error_pct=round(avg_roi_error, 2),
            avg_time_to_sell_minutes=round(avg_time, 1),
            avg_actual_profit=round(statistics.mean(actual_profits), 2) if actual_profits else 0.0,
            avg_expected_profit=round(statistics.mean(expected_profits), 2) if expected_profits else 0.0,
            window_days=window_days,
        )

        logger.info(
            "Precision metrics: executed=%d successful=%d precision=%.2f "
            "avg_profit_error=%.1f%% avg_time=%.0fmin",
            total_executed, total_successful, precision,
            avg_profit_error, avg_time,
        )

        return metrics
