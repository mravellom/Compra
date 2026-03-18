"""
Tests — TruthEvaluator: profit comparison, success detection, precision metrics.
"""
import pytest

from truth_engine.evaluator import TruthEvaluator
from truth_engine.models import TradeOutcome
from datetime import datetime, timezone


def _outcome(
    estimated_profit: float = 50.0,
    actual_profit: float = 40.0,
    expected_roi: float = 0.30,
    actual_roi: float = 0.25,
    sold: bool = True,
    time_to_sell_minutes: float | None = 120.0,
    opportunity_id: int = 1,
) -> TradeOutcome:
    return TradeOutcome(
        opportunity_id=opportunity_id,
        detected_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        estimated_profit=estimated_profit,
        actual_profit=actual_profit,
        expected_roi=expected_roi,
        actual_roi=actual_roi,
        sold=sold,
        time_to_sell_minutes=time_to_sell_minutes,
    )


class TestEvaluateOutcome:

    def test_profit_error_positive_overperformance(self):
        ev = TruthEvaluator()
        result = ev.evaluate_outcome(_outcome(estimated_profit=50, actual_profit=60))
        assert result.profit_error_pct == pytest.approx(20.0, abs=0.1)

    def test_profit_error_negative_underperformance(self):
        ev = TruthEvaluator()
        result = ev.evaluate_outcome(_outcome(estimated_profit=50, actual_profit=30))
        assert result.profit_error_pct == pytest.approx(-40.0, abs=0.1)

    def test_profit_error_zero_estimated(self):
        ev = TruthEvaluator()
        result = ev.evaluate_outcome(_outcome(estimated_profit=0, actual_profit=10))
        assert result.profit_error_pct == 100.0

    def test_profit_error_both_zero(self):
        ev = TruthEvaluator()
        result = ev.evaluate_outcome(_outcome(estimated_profit=0, actual_profit=0))
        assert result.profit_error_pct == 0.0

    def test_roi_error_calculation(self):
        ev = TruthEvaluator()
        result = ev.evaluate_outcome(_outcome(expected_roi=0.30, actual_roi=0.20))
        # (0.20 - 0.30) / 0.30 * 100 = -33.33%
        assert result.roi_error_pct == pytest.approx(-33.33, abs=0.1)

    def test_success_requires_profit_and_sold(self):
        ev = TruthEvaluator()
        # Profitable and sold = success
        assert ev.evaluate_outcome(_outcome(actual_profit=10, sold=True)).success is True
        # Profitable but not sold = failure
        assert ev.evaluate_outcome(_outcome(actual_profit=10, sold=False)).success is False
        # Sold but no profit = failure
        assert ev.evaluate_outcome(_outcome(actual_profit=-5, sold=True)).success is False
        # Zero profit and sold = failure
        assert ev.evaluate_outcome(_outcome(actual_profit=0, sold=True)).success is False

    def test_time_to_sell_passed_through(self):
        ev = TruthEvaluator()
        result = ev.evaluate_outcome(_outcome(time_to_sell_minutes=180.0))
        assert result.time_to_sell_minutes == 180.0


class TestComputePrecisionMetrics:

    def test_empty_outcomes(self):
        ev = TruthEvaluator()
        m = ev.compute_precision_metrics([], total_detected=100)
        assert m.total_detected == 100
        assert m.total_executed == 0
        assert m.precision == 0.0

    def test_all_successful(self):
        ev = TruthEvaluator()
        outcomes = [
            _outcome(actual_profit=30, sold=True, opportunity_id=i)
            for i in range(5)
        ]
        m = ev.compute_precision_metrics(outcomes, total_detected=50)
        assert m.total_executed == 5
        assert m.total_successful == 5
        assert m.precision == 1.0

    def test_mixed_results(self):
        ev = TruthEvaluator()
        outcomes = [
            _outcome(actual_profit=30, sold=True, opportunity_id=1),
            _outcome(actual_profit=20, sold=True, opportunity_id=2),
            _outcome(actual_profit=-5, sold=False, opportunity_id=3),
            _outcome(actual_profit=0, sold=False, opportunity_id=4),
        ]
        m = ev.compute_precision_metrics(outcomes, total_detected=100)
        assert m.total_executed == 4
        assert m.total_successful == 2
        assert m.total_failed == 2
        assert m.precision == pytest.approx(0.50, abs=0.01)

    def test_avg_time_to_sell(self):
        ev = TruthEvaluator()
        outcomes = [
            _outcome(time_to_sell_minutes=60, opportunity_id=1),
            _outcome(time_to_sell_minutes=120, opportunity_id=2),
            _outcome(time_to_sell_minutes=None, opportunity_id=3),
        ]
        m = ev.compute_precision_metrics(outcomes)
        # Average of 60 and 120 (None excluded) = 90
        assert m.avg_time_to_sell_minutes == pytest.approx(90.0, abs=0.1)

    def test_avg_profit_error(self):
        ev = TruthEvaluator()
        outcomes = [
            _outcome(estimated_profit=50, actual_profit=40, opportunity_id=1),  # -20%
            _outcome(estimated_profit=50, actual_profit=60, opportunity_id=2),  # +20%
        ]
        m = ev.compute_precision_metrics(outcomes)
        assert m.avg_profit_error_pct == pytest.approx(0.0, abs=0.1)
