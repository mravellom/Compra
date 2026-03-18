"""
Tests — Latency Tracker: pipeline delay measurement and penalty.
"""
import pytest
from datetime import datetime, timedelta, timezone

from execution_realism.config import LatencyConfig
from execution_realism.latency_tracker import LatencyTracker


class TestLatencyAcceptability:

    def test_fresh_opportunity_passes(self):
        t = LatencyTracker(LatencyConfig(max_latency_seconds=300))
        now = datetime.now(timezone.utc)
        result = t.evaluate(
            detected_at=now - timedelta(seconds=30),
            execution_at=now,
        )
        assert result.is_acceptable is True
        assert result.latency_penalty == 0.0
        assert result.latency_seconds == pytest.approx(30, abs=1)

    def test_stale_opportunity_rejects(self):
        t = LatencyTracker(LatencyConfig(max_latency_seconds=300))
        now = datetime.now(timezone.utc)
        result = t.evaluate(
            detected_at=now - timedelta(seconds=600),
            execution_at=now,
        )
        assert result.is_acceptable is False
        assert "exceeds max" in result.reason


class TestLatencyPenalty:

    def test_no_penalty_below_warning(self):
        t = LatencyTracker(LatencyConfig(warning_latency_seconds=120))
        now = datetime.now(timezone.utc)
        result = t.evaluate(
            detected_at=now - timedelta(seconds=60),
            execution_at=now,
        )
        assert result.latency_penalty == 0.0

    def test_penalty_above_warning(self):
        t = LatencyTracker(LatencyConfig(
            warning_latency_seconds=120,
            max_latency_seconds=600,
            latency_penalty_per_minute=0.05,
        ))
        now = datetime.now(timezone.utc)
        # 240s total, 120s above warning = 2 minutes over
        result = t.evaluate(
            detected_at=now - timedelta(seconds=240),
            execution_at=now,
        )
        assert result.is_acceptable is True
        assert result.latency_penalty == pytest.approx(0.10, abs=0.01)

    def test_penalty_caps_at_one(self):
        t = LatencyTracker(LatencyConfig(
            warning_latency_seconds=60,
            max_latency_seconds=9999,
            latency_penalty_per_minute=0.10,
        ))
        now = datetime.now(timezone.utc)
        # Way over warning but under max
        result = t.evaluate(
            detected_at=now - timedelta(seconds=3600),
            execution_at=now,
        )
        assert result.latency_penalty <= 1.0


class TestTimestamps:

    def test_stores_all_timestamps(self):
        t = LatencyTracker()
        now = datetime.now(timezone.utc)
        det = now - timedelta(seconds=90)
        dec = now - timedelta(seconds=30)

        result = t.evaluate(detected_at=det, decision_at=dec, execution_at=now)
        assert result.detected_at == det
        assert result.decision_at == dec
        assert result.execution_at == now
