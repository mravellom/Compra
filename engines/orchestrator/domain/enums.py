from enum import Enum


class PipelinePhase(str, Enum):
    """Ordered phases in the intelligence pipeline."""
    PREDICTION = "prediction"
    TREND = "trend"
    DECISION = "decision"
    EXECUTION = "execution"


class PhaseStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"       # some phases failed but pipeline produced a result
    FAILED = "failed"


class DecisionType(str, Enum):
    EXECUTE = "execute"       # strong signal → recommend execution
    MONITOR = "monitor"       # moderate signal → watch but don't act
    SKIP = "skip"             # weak signal → no action
    HOLD = "hold"             # conflicting signals → wait for clarity


class SignalStrength(str, Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    CONFLICTING = "conflicting"
