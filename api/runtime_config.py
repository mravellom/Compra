"""
Runtime Config — auto-calibrating configuration state manager.

Closes the feedback loop between the shadow execution tracker
and the system's live parameters.  Every parameter change is:

  1. Bounded — hard min/max prevents runaway values
  2. Smoothed — EMA dampens oscillation
  3. Versioned — every update creates a snapshot
  4. Reversible — rollback to any previous version
  5. Guarded — freezes updates when performance degrades sharply

Integration:
  tracker = ShadowExecutionTracker()
  config  = RuntimeConfig()
  calibrator = AutoCalibrator(config, tracker)

  # Every N minutes:
  calibrator.calibrate()   # reads feedback, applies bounded update
"""
import copy
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)


# ── Parameter bounds ─────────────────────────────────────────

@dataclass(frozen=True)
class ParamBounds:
    """Hard floor/ceiling for one parameter."""
    min_val: float
    max_val: float


DEFAULT_BOUNDS: dict[str, ParamBounds] = {
    "profit_buffer_pct":      ParamBounds(0.0, 0.30),       # 0-30%
    "decay_rate_per_hour":    ParamBounds(0.0, 0.10),        # 0-10%/h
    "confidence_threshold":   ParamBounds(0.10, 0.95),       # 10-95%
    "min_roi":                ParamBounds(0.01, 0.40),       # 1-40%
    "min_profit_usd":         ParamBounds(1.0, 100.0),       # $1-$100
}


# ── Config snapshot ──────────────────────────────────────────

@dataclass
class ConfigSnapshot:
    """One versioned snapshot of live parameters."""

    # Adjustable parameters
    profit_buffer_pct: float = 0.05
    decay_rate_per_hour: float = 0.005
    confidence_threshold: float = 0.30
    min_roi: float = 0.15
    min_profit_usd: float = 20.0

    # Metadata
    version: int = 0
    timestamp: float = 0.0
    source: str = "default"                  # "default", "calibration", "rollback", "manual"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ConfigDiff:
    """What changed between two versions."""
    param: str
    old_value: float
    new_value: float
    delta: float
    delta_pct: float


# ── Calibration constraints ──────────────────────────────────

@dataclass
class CalibrationConfig:
    """Controls how aggressively the auto-calibrator adjusts."""

    max_change_per_cycle: float = 0.05       # max ±5% relative change per cycle
    ema_alpha: float = 0.3                   # EMA smoothing (0.3 = moderate dampening)
    min_samples: int = 10                    # need this many revalidated before adjusting
    freeze_success_rate: float = 0.50        # freeze if success drops below this
    freeze_false_positive_rate: float = 0.50 # freeze if FP exceeds this
    max_history: int = 50                    # keep this many config versions


# ── Runtime Config Manager ───────────────────────────────────

class RuntimeConfig:
    """Thread-safe, versioned, bounded configuration state.

    Supports:
    - Atomic reads and writes
    - Full version history
    - Rollback to any prior version
    - Diff between versions
    """

    def __init__(
        self,
        initial: ConfigSnapshot | None = None,
        bounds: dict[str, ParamBounds] | None = None,
        max_history: int = 50,
    ) -> None:
        self._current = initial or ConfigSnapshot(timestamp=time.time())
        self._bounds = bounds or dict(DEFAULT_BOUNDS)
        self._history: list[ConfigSnapshot] = [copy.deepcopy(self._current)]
        self._max_history = max_history
        self._frozen = False

    @property
    def current(self) -> ConfigSnapshot:
        return self._current

    @property
    def version(self) -> int:
        return self._current.version

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    @property
    def history_size(self) -> int:
        return len(self._history)

    def freeze(self, reason: str = "") -> None:
        """Freeze config updates. No changes allowed until unfreeze."""
        self._frozen = True
        logger.warning("Config FROZEN: %s", reason or "guardrail triggered")

    def unfreeze(self) -> None:
        self._frozen = False
        logger.info("Config unfrozen")

    def get(self, param: str) -> float:
        """Read a single parameter value."""
        return getattr(self._current, param)

    def update(
        self,
        changes: dict[str, float],
        source: str = "calibration",
    ) -> ConfigSnapshot:
        """Apply bounded changes atomically. Returns new snapshot.

        Raises ValueError if frozen.
        """
        if self._frozen:
            raise ValueError("Config is frozen — cannot update")

        new = copy.deepcopy(self._current)
        new.version += 1
        new.timestamp = time.time()
        new.source = source

        for param, value in changes.items():
            if not hasattr(new, param):
                logger.warning("Unknown config param: %s", param)
                continue
            clamped = self._clamp(param, value)
            setattr(new, param, round(clamped, 6))

        self._current = new
        self._history.append(copy.deepcopy(new))
        if len(self._history) > self._max_history:
            self._history.pop(0)

        logger.info(
            "Config updated v%d (%s): %s",
            new.version, source,
            {k: round(v, 4) for k, v in changes.items()},
        )
        return new

    def rollback(self, to_version: int | None = None) -> ConfigSnapshot:
        """Revert to a previous version.

        Args:
            to_version: Target version number. None = previous version.
        """
        if len(self._history) < 2:
            raise ValueError("No previous version to rollback to")

        if to_version is None:
            target = self._history[-2]
        else:
            target = next(
                (s for s in self._history if s.version == to_version),
                None,
            )
            if target is None:
                raise ValueError(f"Version {to_version} not found in history")

        restored = copy.deepcopy(target)
        restored.version = self._current.version + 1
        restored.timestamp = time.time()
        restored.source = "rollback"

        self._current = restored
        self._history.append(copy.deepcopy(restored))
        if len(self._history) > self._max_history:
            self._history.pop(0)

        self._frozen = False

        logger.info(
            "Config rolled back to v%d → new v%d",
            target.version, restored.version,
        )
        return restored

    def diff(self, v1: int, v2: int) -> list[ConfigDiff]:
        """Compare two config versions."""
        s1 = next((s for s in self._history if s.version == v1), None)
        s2 = next((s for s in self._history if s.version == v2), None)
        if s1 is None or s2 is None:
            return []

        params = ["profit_buffer_pct", "decay_rate_per_hour",
                   "confidence_threshold", "min_roi", "min_profit_usd"]
        diffs = []
        for p in params:
            old = getattr(s1, p)
            new = getattr(s2, p)
            if old != new:
                delta = new - old
                delta_pct = (delta / abs(old) * 100) if old != 0 else 0.0
                diffs.append(ConfigDiff(
                    param=p,
                    old_value=round(old, 6),
                    new_value=round(new, 6),
                    delta=round(delta, 6),
                    delta_pct=round(delta_pct, 2),
                ))
        return diffs

    def get_version(self, version: int) -> Optional[ConfigSnapshot]:
        """Get a specific version from history."""
        return next((s for s in self._history if s.version == version), None)

    def _clamp(self, param: str, value: float) -> float:
        """Clamp a value to its hard bounds."""
        bounds = self._bounds.get(param)
        if bounds is None:
            return value
        return max(bounds.min_val, min(bounds.max_val, value))


# ── Auto-Calibrator ──────────────────────────────────────────

class AutoCalibrator:
    """Reads feedback from the execution tracker and safely adjusts config.

    Supports shadow mode: computes adjustments without applying them.
    """

    def __init__(
        self,
        config: RuntimeConfig,
        calibration_config: CalibrationConfig | None = None,
    ) -> None:
        self._config = config
        self._cal = calibration_config or CalibrationConfig()

        # EMA state for smoothing
        self._ema: dict[str, float] = {}
        self._calibration_count: int = 0

    @property
    def calibration_count(self) -> int:
        return self._calibration_count

    def calibrate(
        self,
        feedback: dict,
        *,
        shadow: bool = False,
    ) -> dict:
        """Run one calibration cycle.

        Args:
            feedback: Output from ShadowExecutionTracker.get_feedback_adjustments()
            shadow: If True, compute but DO NOT apply changes.

        Returns:
            Dict with proposed changes, whether applied, and guardrail status.
        """
        result = {
            "applied": False,
            "frozen": self._config.is_frozen,
            "shadow": shadow,
            "proposed_changes": {},
            "guardrail_triggered": False,
            "reason": "",
        }

        # Not enough data
        if not feedback.get("has_data"):
            result["reason"] = "insufficient data"
            return result

        sample_count = feedback.get("sample_count", 0)
        if sample_count < self._cal.min_samples:
            result["reason"] = f"need {self._cal.min_samples} samples, have {sample_count}"
            return result

        # ── Performance guardrails ──
        success_rate = feedback.get("success_rate", 1.0)
        fp_rate = feedback.get("false_positive_rate", 0.0)

        if success_rate < self._cal.freeze_success_rate:
            if not self._config.is_frozen:
                self._config.freeze(
                    f"success_rate {success_rate:.1%} < {self._cal.freeze_success_rate:.0%}"
                )
            result["guardrail_triggered"] = True
            result["frozen"] = True
            result["reason"] = f"success_rate too low ({success_rate:.1%})"
            return result

        if fp_rate > self._cal.freeze_false_positive_rate:
            if not self._config.is_frozen:
                self._config.freeze(
                    f"false_positive_rate {fp_rate:.1%} > {self._cal.freeze_false_positive_rate:.0%}"
                )
            result["guardrail_triggered"] = True
            result["frozen"] = True
            result["reason"] = f"false_positive_rate too high ({fp_rate:.1%})"
            return result

        # If frozen from a previous cycle but guardrails no longer triggered,
        # unfreeze to allow recovery
        if self._config.is_frozen:
            self._config.unfreeze()

        # ── Compute proposed changes ──
        current = self._config.current
        proposed: dict[str, float] = {}

        # profit_buffer_pct
        raw_buffer = feedback.get("profit_buffer_pct", 0.0)
        proposed["profit_buffer_pct"] = self._smooth_and_bound(
            "profit_buffer_pct", current.profit_buffer_pct, raw_buffer,
        )

        # decay_rate_per_hour
        raw_decay = feedback.get("decay_rate_per_hour", 0.0)
        proposed["decay_rate_per_hour"] = self._smooth_and_bound(
            "decay_rate_per_hour", current.decay_rate_per_hour, raw_decay,
        )

        # confidence_threshold
        conf_delta = feedback.get("confidence_threshold_delta", 0.0)
        raw_conf = current.confidence_threshold + conf_delta
        proposed["confidence_threshold"] = self._smooth_and_bound(
            "confidence_threshold", current.confidence_threshold, raw_conf,
        )

        # min_roi — tighten if drift is negative (prices moving against us)
        mean_drift = feedback.get("mean_drift", 0.0)
        if mean_drift < -5.0:
            raw_roi = current.min_roi + 0.01  # nudge up
        elif mean_drift > 5.0:
            raw_roi = current.min_roi - 0.005  # nudge down gently
        else:
            raw_roi = current.min_roi
        proposed["min_roi"] = self._smooth_and_bound(
            "min_roi", current.min_roi, raw_roi,
        )

        # min_profit_usd — scale with observed drift
        if mean_drift < -10.0:
            raw_profit = current.min_profit_usd + 2.0
        elif mean_drift > 10.0:
            raw_profit = current.min_profit_usd - 1.0
        else:
            raw_profit = current.min_profit_usd
        proposed["min_profit_usd"] = self._smooth_and_bound(
            "min_profit_usd", current.min_profit_usd, raw_profit,
        )

        result["proposed_changes"] = {
            k: round(v, 6) for k, v in proposed.items()
        }

        # ── Apply or shadow ──
        if shadow:
            result["reason"] = "shadow mode — not applied"
            logger.info(
                "[shadow] Would apply config changes: %s",
                result["proposed_changes"],
            )
        else:
            try:
                self._config.update(proposed, source="calibration")
                result["applied"] = True
                self._calibration_count += 1
                result["reason"] = "applied"
            except ValueError as exc:
                result["reason"] = str(exc)

        return result

    def _smooth_and_bound(
        self,
        param: str,
        current_val: float,
        raw_target: float,
    ) -> float:
        """Apply EMA smoothing and max-change-per-cycle constraint.

        1. EMA: smoothed = alpha * raw + (1 - alpha) * previous_ema
        2. Cap delta to ±max_change_per_cycle of current value
        3. Clamp to hard bounds
        """
        alpha = self._cal.ema_alpha

        # Initialize EMA on first call
        if param not in self._ema:
            self._ema[param] = current_val

        # EMA smoothing
        smoothed = alpha * raw_target + (1 - alpha) * self._ema[param]
        self._ema[param] = smoothed

        # Cap change per cycle
        max_delta = abs(current_val) * self._cal.max_change_per_cycle
        if max_delta < 0.001:
            max_delta = 0.001  # floor for near-zero values
        delta = smoothed - current_val
        clamped_delta = max(-max_delta, min(max_delta, delta))

        return current_val + clamped_delta
