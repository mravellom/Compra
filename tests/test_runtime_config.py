"""
Tests for the Runtime Config auto-calibration system.

Covers:
  1. Config snapshots and atomic reads
  2. Bounded updates (hard clamps)
  3. Versioning and history
  4. Rollback to previous versions
  5. Diff between versions
  6. Freeze / unfreeze guardrails
  7. AutoCalibrator with EMA smoothing
  8. Max change per cycle enforcement
  9. Shadow mode (compute but don't apply)
  10. Performance guardrails (freeze on degradation)
  11. Stability over many cycles (no explosion)
  12. Recovery from frozen state
"""
import time

import pytest

from api.runtime_config import (
    AutoCalibrator,
    CalibrationConfig,
    ConfigDiff,
    ConfigSnapshot,
    ParamBounds,
    RuntimeConfig,
    DEFAULT_BOUNDS,
)


# ── Helpers ──────────────────────────────────────────────────

def _good_feedback(**overrides) -> dict:
    """Feedback dict representing a healthy system."""
    defaults = {
        "has_data": True,
        "profit_buffer_pct": 0.05,
        "decay_rate_per_hour": 0.003,
        "confidence_threshold_delta": 0.0,
        "success_rate": 0.85,
        "false_positive_rate": 0.15,
        "mean_drift": -2.0,
        "sample_count": 50,
    }
    defaults.update(overrides)
    return defaults


def _bad_feedback(**overrides) -> dict:
    """Feedback representing a degrading system."""
    defaults = {
        "has_data": True,
        "profit_buffer_pct": 0.20,
        "decay_rate_per_hour": 0.05,
        "confidence_threshold_delta": 0.05,
        "success_rate": 0.40,
        "false_positive_rate": 0.60,
        "mean_drift": -15.0,
        "sample_count": 50,
    }
    defaults.update(overrides)
    return defaults


# ── ConfigSnapshot Tests ─────────────────────────────────────

class TestConfigSnapshot:

    def test_default_values(self):
        snap = ConfigSnapshot()
        assert snap.profit_buffer_pct == 0.05
        assert snap.decay_rate_per_hour == 0.005
        assert snap.confidence_threshold == 0.30
        assert snap.min_roi == 0.15
        assert snap.min_profit_usd == 20.0
        assert snap.version == 0
        assert snap.source == "default"

    def test_to_dict(self):
        snap = ConfigSnapshot()
        d = snap.to_dict()
        assert "profit_buffer_pct" in d
        assert "version" in d
        assert d["min_roi"] == 0.15


# ── RuntimeConfig Tests ──────────────────────────────────────

class TestRuntimeConfig:

    def test_initial_version_zero(self):
        cfg = RuntimeConfig()
        assert cfg.version == 0
        assert cfg.current.source == "default"

    def test_get_parameter(self):
        cfg = RuntimeConfig()
        assert cfg.get("min_roi") == 0.15
        assert cfg.get("min_profit_usd") == 20.0

    def test_update_increments_version(self):
        cfg = RuntimeConfig()
        cfg.update({"min_roi": 0.20})
        assert cfg.version == 1
        assert cfg.get("min_roi") == 0.20

    def test_update_clamps_to_bounds(self):
        cfg = RuntimeConfig()
        # Try to set min_roi above max bound (0.40)
        cfg.update({"min_roi": 0.99})
        assert cfg.get("min_roi") == 0.40
        # Try below min bound (0.01)
        cfg.update({"min_roi": -1.0})
        assert cfg.get("min_roi") == 0.01

    def test_update_clamps_profit(self):
        cfg = RuntimeConfig()
        cfg.update({"min_profit_usd": 500.0})
        assert cfg.get("min_profit_usd") == 100.0
        cfg.update({"min_profit_usd": -10.0})
        assert cfg.get("min_profit_usd") == 1.0

    def test_update_records_source(self):
        cfg = RuntimeConfig()
        cfg.update({"min_roi": 0.20}, source="manual")
        assert cfg.current.source == "manual"

    def test_unknown_param_ignored(self):
        cfg = RuntimeConfig()
        cfg.update({"nonexistent_param": 42.0})
        assert cfg.version == 1  # version still increments

    def test_history_grows(self):
        cfg = RuntimeConfig()
        for i in range(5):
            cfg.update({"min_roi": 0.15 + i * 0.01})
        assert cfg.history_size == 6  # initial + 5 updates

    def test_history_bounded(self):
        cfg = RuntimeConfig(max_history=5)
        for i in range(20):
            cfg.update({"min_roi": 0.15 + i * 0.001})
        assert cfg.history_size <= 5


# ── Versioning Tests ─────────────────────────────────────────

class TestVersioning:

    def test_versions_are_sequential(self):
        cfg = RuntimeConfig()
        cfg.update({"min_roi": 0.16})
        cfg.update({"min_roi": 0.17})
        cfg.update({"min_roi": 0.18})
        assert cfg.version == 3

    def test_get_version(self):
        cfg = RuntimeConfig()
        cfg.update({"min_roi": 0.20})
        v0 = cfg.get_version(0)
        v1 = cfg.get_version(1)
        assert v0 is not None
        assert v1 is not None
        assert v0.min_roi == 0.15
        assert v1.min_roi == 0.20

    def test_get_missing_version(self):
        cfg = RuntimeConfig()
        assert cfg.get_version(999) is None

    def test_timestamp_increases(self):
        cfg = RuntimeConfig()
        t0 = cfg.current.timestamp
        cfg.update({"min_roi": 0.20})
        t1 = cfg.current.timestamp
        assert t1 >= t0


# ── Diff Tests ───────────────────────────────────────────────

class TestDiff:

    def test_diff_shows_changes(self):
        cfg = RuntimeConfig()
        cfg.update({"min_roi": 0.20, "min_profit_usd": 25.0})
        diffs = cfg.diff(0, 1)
        params_changed = {d.param for d in diffs}
        assert "min_roi" in params_changed
        assert "min_profit_usd" in params_changed

    def test_diff_no_changes(self):
        cfg = RuntimeConfig()
        cfg.update({"min_roi": 0.15})  # same as default
        diffs = cfg.diff(0, 1)
        assert len(diffs) == 0

    def test_diff_delta_correct(self):
        cfg = RuntimeConfig()
        cfg.update({"min_roi": 0.20})
        diffs = cfg.diff(0, 1)
        roi_diff = next(d for d in diffs if d.param == "min_roi")
        assert roi_diff.delta == pytest.approx(0.05, abs=0.001)
        assert roi_diff.delta_pct == pytest.approx(33.33, abs=0.1)

    def test_diff_missing_version(self):
        cfg = RuntimeConfig()
        assert cfg.diff(0, 999) == []


# ── Rollback Tests ───────────────────────────────────────────

class TestRollback:

    def test_rollback_to_previous(self):
        cfg = RuntimeConfig()
        cfg.update({"min_roi": 0.20})
        cfg.update({"min_roi": 0.25})
        assert cfg.get("min_roi") == 0.25

        cfg.rollback()
        assert cfg.get("min_roi") == 0.20
        assert cfg.current.source == "rollback"

    def test_rollback_to_specific_version(self):
        cfg = RuntimeConfig()
        cfg.update({"min_roi": 0.20})
        cfg.update({"min_roi": 0.25})
        cfg.update({"min_roi": 0.30})

        cfg.rollback(to_version=1)
        assert cfg.get("min_roi") == 0.20

    def test_rollback_increments_version(self):
        cfg = RuntimeConfig()
        cfg.update({"min_roi": 0.20})
        old_version = cfg.version
        cfg.rollback()
        assert cfg.version == old_version + 1

    def test_rollback_no_history_raises(self):
        cfg = RuntimeConfig()
        with pytest.raises(ValueError, match="No previous version"):
            cfg.rollback()

    def test_rollback_missing_version_raises(self):
        cfg = RuntimeConfig()
        cfg.update({"min_roi": 0.20})
        with pytest.raises(ValueError, match="not found"):
            cfg.rollback(to_version=999)

    def test_rollback_unfreezes(self):
        cfg = RuntimeConfig()
        cfg.update({"min_roi": 0.20})
        cfg.freeze("test")
        assert cfg.is_frozen
        cfg.rollback()
        assert not cfg.is_frozen


# ── Freeze / Unfreeze Tests ──────────────────────────────────

class TestFreezeGuardrails:

    def test_frozen_config_rejects_updates(self):
        cfg = RuntimeConfig()
        cfg.freeze("test")
        with pytest.raises(ValueError, match="frozen"):
            cfg.update({"min_roi": 0.20})

    def test_unfreeze_allows_updates(self):
        cfg = RuntimeConfig()
        cfg.freeze()
        cfg.unfreeze()
        cfg.update({"min_roi": 0.20})
        assert cfg.get("min_roi") == 0.20

    def test_is_frozen_property(self):
        cfg = RuntimeConfig()
        assert not cfg.is_frozen
        cfg.freeze()
        assert cfg.is_frozen
        cfg.unfreeze()
        assert not cfg.is_frozen


# ── AutoCalibrator Tests ─────────────────────────────────────

class TestAutoCalibrator:

    def test_insufficient_data_skips(self):
        cfg = RuntimeConfig()
        cal = AutoCalibrator(cfg)
        result = cal.calibrate({"has_data": False})
        assert not result["applied"]
        assert "insufficient" in result["reason"]

    def test_too_few_samples_skips(self):
        cfg = RuntimeConfig()
        cal = AutoCalibrator(cfg, CalibrationConfig(min_samples=20))
        result = cal.calibrate(_good_feedback(sample_count=5))
        assert not result["applied"]
        assert "need 20" in result["reason"]

    def test_healthy_feedback_applies(self):
        cfg = RuntimeConfig()
        cal = AutoCalibrator(cfg)
        result = cal.calibrate(_good_feedback())
        assert result["applied"] is True
        assert cfg.version == 1

    def test_shadow_mode_does_not_apply(self):
        cfg = RuntimeConfig()
        cal = AutoCalibrator(cfg)
        result = cal.calibrate(_good_feedback(), shadow=True)
        assert not result["applied"]
        assert result["shadow"] is True
        assert cfg.version == 0  # unchanged
        assert "proposed_changes" in result
        assert len(result["proposed_changes"]) > 0

    def test_calibration_count_tracks(self):
        cfg = RuntimeConfig()
        cal = AutoCalibrator(cfg)
        cal.calibrate(_good_feedback())
        cal.calibrate(_good_feedback())
        assert cal.calibration_count == 2


# ── EMA Smoothing Tests ──────────────────────────────────────

class TestEMASmoothing:

    def test_ema_dampens_large_changes(self):
        cfg = RuntimeConfig()
        cal = AutoCalibrator(cfg, CalibrationConfig(ema_alpha=0.3))

        # First calibration with large buffer request
        result = cal.calibrate(_good_feedback(profit_buffer_pct=0.25))
        # EMA should dampen: not jump straight to 0.25
        new_buffer = cfg.get("profit_buffer_pct")
        assert new_buffer < 0.25
        assert new_buffer > 0.05  # moved but dampened

    def test_repeated_stable_feedback_converges(self):
        cfg = RuntimeConfig()
        cal = AutoCalibrator(cfg, CalibrationConfig(ema_alpha=0.3))
        target_buffer = 0.10

        for _ in range(20):
            cal.calibrate(_good_feedback(profit_buffer_pct=target_buffer))

        # After many cycles, should converge toward target
        actual = cfg.get("profit_buffer_pct")
        assert abs(actual - target_buffer) < 0.02  # within 2% of target


# ── Max Change Per Cycle Tests ───────────────────────────────

class TestMaxChangePerCycle:

    def test_change_capped(self):
        cfg = RuntimeConfig(initial=ConfigSnapshot(min_roi=0.15))
        cal = AutoCalibrator(cfg, CalibrationConfig(
            max_change_per_cycle=0.05,
            ema_alpha=1.0,  # no smoothing — raw change
        ))

        # Request jump from 0.15 to 0.30 (100% increase)
        result = cal.calibrate(_good_feedback(mean_drift=-20.0))
        new_roi = cfg.get("min_roi")

        # Max change is 5% of 0.15 = 0.0075 per cycle
        assert new_roi <= 0.15 + 0.0075 + 0.001  # small tolerance


# ── Performance Guardrails Tests ─────────────────────────────

class TestPerformanceGuardrails:

    def test_low_success_rate_freezes(self):
        cfg = RuntimeConfig()
        cal = AutoCalibrator(cfg)
        result = cal.calibrate(_bad_feedback(success_rate=0.30))
        assert result["guardrail_triggered"] is True
        assert result["frozen"] is True
        assert cfg.is_frozen

    def test_high_fp_rate_freezes(self):
        cfg = RuntimeConfig()
        cal = AutoCalibrator(cfg)
        result = cal.calibrate(_bad_feedback(
            success_rate=0.70,  # OK
            false_positive_rate=0.60,  # bad
        ))
        assert result["guardrail_triggered"] is True
        assert cfg.is_frozen

    def test_recovery_after_guardrail(self):
        cfg = RuntimeConfig()
        cal = AutoCalibrator(cfg)

        # First: bad feedback freezes
        cal.calibrate(_bad_feedback(success_rate=0.30))
        assert cfg.is_frozen

        # Second: good feedback unfreezes and applies
        result = cal.calibrate(_good_feedback())
        assert not cfg.is_frozen
        assert result["applied"] is True

    def test_frozen_from_previous_blocks(self):
        cfg = RuntimeConfig()
        cal = AutoCalibrator(cfg)
        cal.calibrate(_bad_feedback(success_rate=0.30))

        # Still bad feedback — stays frozen
        result = cal.calibrate(_bad_feedback(success_rate=0.35))
        assert result["guardrail_triggered"] is True
        assert not result["applied"]


# ── Stability Over Time Tests ────────────────────────────────

class TestStability:

    def test_no_explosion_over_many_cycles(self):
        """Parameters must stay within bounds no matter how many cycles."""
        cfg = RuntimeConfig()
        cal = AutoCalibrator(cfg)

        for i in range(100):
            # Alternating feedback to stress-test oscillation
            if i % 3 == 0:
                cal.calibrate(_good_feedback(
                    profit_buffer_pct=0.25,
                    mean_drift=-15.0,
                ))
            elif i % 3 == 1:
                cal.calibrate(_good_feedback(
                    profit_buffer_pct=0.01,
                    mean_drift=5.0,
                ))
            else:
                cal.calibrate(_good_feedback())

        # All params must be within bounds
        snap = cfg.current
        assert 0.0 <= snap.profit_buffer_pct <= 0.30
        assert 0.0 <= snap.decay_rate_per_hour <= 0.10
        assert 0.10 <= snap.confidence_threshold <= 0.95
        assert 0.01 <= snap.min_roi <= 0.40
        assert 1.0 <= snap.min_profit_usd <= 100.0

    def test_stable_feedback_produces_stable_config(self):
        """Identical feedback should converge, not oscillate."""
        cfg = RuntimeConfig()
        cal = AutoCalibrator(cfg)
        feedback = _good_feedback()

        values = []
        for _ in range(30):
            cal.calibrate(feedback)
            values.append(cfg.get("profit_buffer_pct"))

        # Last 10 values should have very low variance (converged)
        last_10 = values[-10:]
        spread = max(last_10) - min(last_10)
        assert spread < 0.01  # converged within 1%


# ── End-to-End Integration ───────────────────────────────────

class TestEndToEnd:

    def test_full_lifecycle(self):
        """Record → calibrate → rollback → recalibrate."""
        cfg = RuntimeConfig()
        cal = AutoCalibrator(cfg)

        # V0: defaults
        assert cfg.version == 0

        # V1: first calibration
        cal.calibrate(_good_feedback())
        assert cfg.version == 1
        v1_roi = cfg.get("min_roi")

        # V2: another calibration
        cal.calibrate(_good_feedback(mean_drift=-12.0))
        assert cfg.version == 2

        # V3: rollback to V1
        cfg.rollback(to_version=1)
        assert cfg.version == 3
        assert cfg.get("min_roi") == v1_roi

        # V4: recalibrate from rolled-back state
        cal.calibrate(_good_feedback())
        assert cfg.version == 4

    def test_shadow_then_apply(self):
        """Shadow mode first, then apply if looks good."""
        cfg = RuntimeConfig()
        cal = AutoCalibrator(cfg)

        # Shadow: see what would happen
        shadow_result = cal.calibrate(_good_feedback(), shadow=True)
        assert not shadow_result["applied"]
        assert cfg.version == 0
        proposed = shadow_result["proposed_changes"]

        # Looks good → apply for real
        result = cal.calibrate(_good_feedback())
        assert result["applied"]
        assert cfg.version == 1
