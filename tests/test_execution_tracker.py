"""
Tests for the Shadow Execution Tracker.

Covers:
  1. Recording opportunities
  2. Delayed re-validation with real prices
  3. Profit drift calculation accuracy
  4. Success rate / false positive tracking
  5. Category-level decay measurement
  6. Alert thresholds
  7. Feedback loop adjustments
  8. Bounded history (deque overflow)
  9. Edge cases (zero profit, missing entries, duplicate revalidation)
"""
import time

import pytest

from api.execution_tracker import (
    AlertStatus,
    CategoryDecay,
    DriftStats,
    ShadowExecutionTracker,
    SuccessMetrics,
    TrackedOpportunity,
    TrackerConfig,
)


@pytest.fixture
def tracker():
    t = ShadowExecutionTracker()
    yield t
    t.clear()


def _record_and_revalidate(
    tracker: ShadowExecutionTracker,
    opp_id: int = 1,
    predicted_profit: float = 50.0,
    real_buy_delta: float = 0.0,
    real_sell_delta: float = 0.0,
    category: str = "headphones",
    **record_kw,
):
    """Helper: record + immediately revalidate with price deltas."""
    defaults = dict(
        opportunity_id=opp_id,
        product_id=opp_id * 10,
        buy_price_predicted=100.0,
        sell_price_predicted=100.0 + predicted_profit + 25.0,  # +fees headroom
        expected_profit=predicted_profit,
        expected_roi=predicted_profit / 100.0,
        validation_confidence=0.80,
        buy_marketplace="amazon_us",
        sell_marketplace="mercadolibre_mx",
        category=category,
    )
    defaults.update(record_kw)
    entry = tracker.record(**defaults)
    tracker.revalidate(
        opp_id,
        buy_price_real=defaults["buy_price_predicted"] + real_buy_delta,
        sell_price_real=defaults["sell_price_predicted"] + real_sell_delta,
    )
    return entry


# ── Step 1: Recording ───────────────────────────────────────

class TestRecording:

    def test_record_stores_entry(self, tracker):
        entry = tracker.record(
            opportunity_id=1, product_id=10,
            buy_price_predicted=80.0, sell_price_predicted=150.0,
            expected_profit=45.0, expected_roi=0.56,
            validation_confidence=0.75,
        )
        assert tracker.tracked_count == 1
        assert entry.opportunity_id == 1
        assert entry.expected_profit == 45.0
        assert entry.revalidated is False

    def test_multiple_records(self, tracker):
        for i in range(5):
            tracker.record(
                opportunity_id=i, product_id=i * 10,
                buy_price_predicted=80.0, sell_price_predicted=150.0,
                expected_profit=45.0, expected_roi=0.56,
                validation_confidence=0.75,
            )
        assert tracker.tracked_count == 5

    def test_bounded_history(self):
        cfg = TrackerConfig(max_tracked=10)
        tracker = ShadowExecutionTracker(config=cfg)
        for i in range(20):
            tracker.record(
                opportunity_id=i, product_id=i,
                buy_price_predicted=100.0, sell_price_predicted=200.0,
                expected_profit=50.0, expected_roi=0.50,
                validation_confidence=0.80,
            )
        assert tracker.tracked_count == 10


# ── Step 2: Delayed Re-validation ────────────────────────────

class TestRevalidation:

    def test_revalidation_fills_real_prices(self, tracker):
        tracker.record(
            opportunity_id=1, product_id=10,
            buy_price_predicted=80.0, sell_price_predicted=150.0,
            expected_profit=45.0, expected_roi=0.56,
            validation_confidence=0.80,
        )
        entry = tracker.revalidate(1, buy_price_real=82.0, sell_price_real=148.0)
        assert entry is not None
        assert entry.revalidated is True
        assert entry.buy_price_real == 82.0
        assert entry.sell_price_real == 148.0
        assert entry.delay_seconds > 0

    def test_revalidation_with_real_fees(self, tracker):
        tracker.record(
            opportunity_id=1, product_id=10,
            buy_price_predicted=80.0, sell_price_predicted=150.0,
            expected_profit=45.0, expected_roi=0.56,
            validation_confidence=0.80,
        )
        entry = tracker.revalidate(1, buy_price_real=80.0, sell_price_real=150.0, real_fees=30.0)
        assert entry.real_profit == 40.0  # 150 - 80 - 30

    def test_missing_entry_returns_none(self, tracker):
        result = tracker.revalidate(999, buy_price_real=80.0, sell_price_real=150.0)
        assert result is None

    def test_double_revalidation_is_idempotent(self, tracker):
        tracker.record(
            opportunity_id=1, product_id=10,
            buy_price_predicted=80.0, sell_price_predicted=150.0,
            expected_profit=45.0, expected_roi=0.56,
            validation_confidence=0.80,
        )
        e1 = tracker.revalidate(1, buy_price_real=82.0, sell_price_real=148.0)
        e2 = tracker.revalidate(1, buy_price_real=90.0, sell_price_real=140.0)  # ignored
        assert e1.real_profit == e2.real_profit  # second call returns same

    def test_revalidated_count(self, tracker):
        for i in range(5):
            tracker.record(
                opportunity_id=i, product_id=i,
                buy_price_predicted=80.0, sell_price_predicted=150.0,
                expected_profit=45.0, expected_roi=0.56,
                validation_confidence=0.80,
            )
        tracker.revalidate(0, buy_price_real=80.0, sell_price_real=150.0)
        tracker.revalidate(1, buy_price_real=80.0, sell_price_real=150.0)
        assert tracker.revalidated_count == 2


# ── Step 3: Profit Drift ────────────────────────────────────

class TestDriftStats:

    def test_no_drift_when_prices_stable(self, tracker):
        _record_and_revalidate(tracker, opp_id=1, predicted_profit=50.0)
        drift = tracker.compute_drift_stats()
        assert drift.mean_drift == 0.0
        assert drift.negative_flip_count == 0

    def test_negative_drift_when_buy_increases(self, tracker):
        _record_and_revalidate(
            tracker, opp_id=1, predicted_profit=50.0, real_buy_delta=10.0,
        )
        drift = tracker.compute_drift_stats()
        assert drift.mean_drift < 0  # profit decreased

    def test_positive_drift_when_sell_increases(self, tracker):
        _record_and_revalidate(
            tracker, opp_id=1, predicted_profit=50.0, real_sell_delta=10.0,
        )
        drift = tracker.compute_drift_stats()
        assert drift.mean_drift > 0  # profit increased

    def test_worst_drift_is_minimum(self, tracker):
        _record_and_revalidate(tracker, opp_id=1, predicted_profit=50.0, real_buy_delta=5.0)
        _record_and_revalidate(tracker, opp_id=2, predicted_profit=50.0, real_buy_delta=20.0)
        _record_and_revalidate(tracker, opp_id=3, predicted_profit=50.0, real_sell_delta=5.0)
        drift = tracker.compute_drift_stats()
        assert drift.worst_drift == -20.0  # largest negative

    def test_negative_flip_detected(self, tracker):
        # Predicted $10 profit, but buy went up $15 → real profit = -$5
        _record_and_revalidate(
            tracker, opp_id=1, predicted_profit=10.0, real_buy_delta=15.0,
        )
        drift = tracker.compute_drift_stats()
        assert drift.negative_flip_count == 1
        assert drift.negative_flip_rate == 1.0

    def test_drift_pct_calculated(self, tracker):
        # $50 predicted, real buy goes up $10 → drift = -$10 → drift_pct = -20%
        tracker.record(
            opportunity_id=1, product_id=10,
            buy_price_predicted=100.0, sell_price_predicted=175.0,
            expected_profit=50.0, expected_roi=0.50,
            validation_confidence=0.80,
        )
        tracker.revalidate(1, buy_price_real=110.0, sell_price_real=175.0)
        entry = tracker._by_id[1]
        assert entry.profit_drift_pct == pytest.approx(-20.0, abs=0.1)

    def test_empty_tracker_returns_zero_drift(self, tracker):
        drift = tracker.compute_drift_stats()
        assert drift.sample_count == 0
        assert drift.mean_drift == 0.0


# ── Step 4: Success Rate ────────────────────────────────────

class TestSuccessMetrics:

    def test_all_profitable(self, tracker):
        for i in range(10):
            _record_and_revalidate(tracker, opp_id=i, predicted_profit=50.0)
        metrics = tracker.compute_success_metrics()
        assert metrics.success_rate == 1.0
        assert metrics.false_positive_rate == 0.0
        assert metrics.total_still_profitable == 10

    def test_mixed_results(self, tracker):
        # 7 profitable, 3 flipped negative
        for i in range(7):
            _record_and_revalidate(tracker, opp_id=i, predicted_profit=50.0)
        for i in range(7, 10):
            _record_and_revalidate(
                tracker, opp_id=i, predicted_profit=10.0, real_buy_delta=20.0,
            )
        metrics = tracker.compute_success_metrics()
        assert metrics.success_rate == pytest.approx(0.70, abs=0.01)
        assert metrics.false_positive_rate == pytest.approx(0.30, abs=0.01)

    def test_avg_predicted_vs_real(self, tracker):
        _record_and_revalidate(tracker, opp_id=1, predicted_profit=50.0, real_buy_delta=5.0)
        _record_and_revalidate(tracker, opp_id=2, predicted_profit=30.0, real_sell_delta=10.0)
        metrics = tracker.compute_success_metrics()
        assert metrics.avg_predicted_profit == pytest.approx(40.0, abs=0.01)
        # real profits: (50-5)=45, (30+10)=40 → avg 42.5
        assert metrics.avg_real_profit == pytest.approx(42.5, abs=0.01)

    def test_unrevalidated_not_counted(self, tracker):
        tracker.record(
            opportunity_id=1, product_id=10,
            buy_price_predicted=80.0, sell_price_predicted=150.0,
            expected_profit=45.0, expected_roi=0.56,
            validation_confidence=0.80,
        )
        metrics = tracker.compute_success_metrics()
        assert metrics.total_tracked == 1
        assert metrics.total_revalidated == 0
        assert metrics.success_rate == 0.0


# ── Step 5: Category Decay ──────────────────────────────────

class TestCategoryDecay:

    def test_per_category_measurement(self, tracker):
        _record_and_revalidate(
            tracker, opp_id=1, predicted_profit=50.0,
            real_buy_delta=5.0, category="headphones",
        )
        _record_and_revalidate(
            tracker, opp_id=2, predicted_profit=50.0,
            real_buy_delta=10.0, category="headphones",
        )
        _record_and_revalidate(
            tracker, opp_id=3, predicted_profit=50.0,
            real_buy_delta=1.0, category="tablets",
        )
        decays = tracker.compute_category_decay()
        cats = {d.category: d for d in decays}
        assert "headphones" in cats
        assert "tablets" in cats
        assert cats["headphones"].sample_count == 2
        assert cats["tablets"].sample_count == 1
        # Headphones drifted more
        assert abs(cats["headphones"].avg_drift_pct) > abs(cats["tablets"].avg_drift_pct)

    def test_empty_returns_empty(self, tracker):
        assert tracker.compute_category_decay() == []


# ── Step 6: Alerts ──────────────────────────────────────────

class TestAlerts:

    def test_no_alerts_when_healthy(self, tracker):
        for i in range(10):
            _record_and_revalidate(tracker, opp_id=i, predicted_profit=50.0)
        alerts = tracker.check_alerts()
        assert not alerts.success_rate_alert
        assert not alerts.drift_alert
        assert not alerts.false_positive_alert
        assert len(alerts.messages) == 0

    def test_success_rate_alert(self, tracker):
        # 2 profitable, 8 flipped → 20% success
        for i in range(2):
            _record_and_revalidate(tracker, opp_id=i, predicted_profit=50.0)
        for i in range(2, 10):
            _record_and_revalidate(
                tracker, opp_id=i, predicted_profit=5.0, real_buy_delta=10.0,
            )
        alerts = tracker.check_alerts()
        assert alerts.success_rate_alert is True

    def test_false_positive_alert(self, tracker):
        cfg = TrackerConfig(alert_false_positive_rate=0.20)
        t = ShadowExecutionTracker(config=cfg)
        for i in range(7):
            _record_and_revalidate(t, opp_id=i, predicted_profit=50.0)
        for i in range(7, 10):
            _record_and_revalidate(
                t, opp_id=i, predicted_profit=5.0, real_buy_delta=10.0,
            )
        alerts = t.check_alerts()
        assert alerts.false_positive_alert is True

    def test_not_enough_data_skips_alerts(self, tracker):
        _record_and_revalidate(tracker, opp_id=1, predicted_profit=5.0, real_buy_delta=10.0)
        alerts = tracker.check_alerts()
        # < 5 revalidated → no alerts
        assert len(alerts.messages) == 0


# ── Step 7: Feedback Loop ───────────────────────────────────

class TestFeedbackLoop:

    def test_not_enough_data(self, tracker):
        _record_and_revalidate(tracker, opp_id=1, predicted_profit=50.0)
        feedback = tracker.get_feedback_adjustments()
        assert feedback["has_data"] is False

    def test_stable_system_low_buffer(self, tracker):
        for i in range(15):
            _record_and_revalidate(tracker, opp_id=i, predicted_profit=50.0)
        feedback = tracker.get_feedback_adjustments()
        assert feedback["has_data"] is True
        assert feedback["profit_buffer_pct"] == 0.0  # no negative drift
        assert feedback["confidence_threshold_delta"] <= 0  # relax or neutral

    def test_drifting_system_increases_buffer(self, tracker):
        for i in range(15):
            _record_and_revalidate(
                tracker, opp_id=i, predicted_profit=50.0, real_buy_delta=10.0,
            )
        feedback = tracker.get_feedback_adjustments()
        assert feedback["has_data"] is True
        assert feedback["profit_buffer_pct"] > 0  # needs buffer for negative drift

    def test_high_false_positive_tightens_confidence(self, tracker):
        for i in range(7):
            _record_and_revalidate(tracker, opp_id=i, predicted_profit=50.0)
        for i in range(7, 15):
            _record_and_revalidate(
                tracker, opp_id=i, predicted_profit=5.0, real_buy_delta=10.0,
            )
        feedback = tracker.get_feedback_adjustments()
        # > 30% false positive → tighten confidence
        assert feedback["confidence_threshold_delta"] > 0

    def test_feedback_contains_success_rate(self, tracker):
        for i in range(15):
            _record_and_revalidate(tracker, opp_id=i, predicted_profit=50.0)
        feedback = tracker.get_feedback_adjustments()
        assert "success_rate" in feedback
        assert feedback["success_rate"] == 1.0


# ── Step 8: Pending Re-validation ────────────────────────────

class TestPendingRevalidation:

    def test_nothing_pending_when_fresh(self):
        cfg = TrackerConfig(revalidation_delay_seconds=300)
        t = ShadowExecutionTracker(config=cfg)
        t.record(
            opportunity_id=1, product_id=10,
            buy_price_predicted=80.0, sell_price_predicted=150.0,
            expected_profit=45.0, expected_roi=0.56,
            validation_confidence=0.80,
        )
        assert len(t.get_pending_revalidation()) == 0

    def test_pending_after_delay(self):
        cfg = TrackerConfig(revalidation_delay_seconds=0)  # immediate
        t = ShadowExecutionTracker(config=cfg)
        t.record(
            opportunity_id=1, product_id=10,
            buy_price_predicted=80.0, sell_price_predicted=150.0,
            expected_profit=45.0, expected_roi=0.56,
            validation_confidence=0.80,
        )
        pending = t.get_pending_revalidation()
        assert len(pending) == 1
        assert pending[0].opportunity_id == 1

    def test_already_revalidated_not_pending(self):
        cfg = TrackerConfig(revalidation_delay_seconds=0)
        t = ShadowExecutionTracker(config=cfg)
        t.record(
            opportunity_id=1, product_id=10,
            buy_price_predicted=80.0, sell_price_predicted=150.0,
            expected_profit=45.0, expected_roi=0.56,
            validation_confidence=0.80,
        )
        t.revalidate(1, buy_price_real=80.0, sell_price_real=150.0)
        assert len(t.get_pending_revalidation()) == 0


# ── Edge Cases ──────────────────────────────────────────────

class TestEdgeCases:

    def test_zero_predicted_profit(self, tracker):
        tracker.record(
            opportunity_id=1, product_id=10,
            buy_price_predicted=100.0, sell_price_predicted=125.0,
            expected_profit=0.0, expected_roi=0.0,
            validation_confidence=0.50,
        )
        entry = tracker.revalidate(1, buy_price_real=100.0, sell_price_real=125.0)
        assert entry.profit_drift_pct == 0.0

    def test_zero_buy_price_no_division_error(self, tracker):
        tracker.record(
            opportunity_id=1, product_id=10,
            buy_price_predicted=0.0, sell_price_predicted=50.0,
            expected_profit=50.0, expected_roi=0.0,
            validation_confidence=0.50,
        )
        entry = tracker.revalidate(1, buy_price_real=0.0, sell_price_real=50.0)
        assert entry.real_roi == 0.0

    def test_clear_resets_everything(self, tracker):
        for i in range(5):
            _record_and_revalidate(tracker, opp_id=i, predicted_profit=50.0)
        tracker.clear()
        assert tracker.tracked_count == 0
        assert tracker.revalidated_count == 0
