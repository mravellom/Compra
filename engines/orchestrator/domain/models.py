"""Domain models for the Market Intelligence Orchestrator.

All models are pure Pydantic — no DB, no framework dependencies.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from .enums import (
    DecisionType,
    PhaseStatus,
    PipelinePhase,
    PipelineStatus,
    SignalStrength,
)


class PhaseResult(BaseModel):
    """Outcome of a single pipeline phase."""
    phase: PipelinePhase
    status: PhaseStatus = PhaseStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    retries: int = 0
    data: dict = Field(default_factory=dict)
    error: Optional[str] = None


class PredictionSignal(BaseModel):
    """Signal extracted from the prediction engine for decision-making."""
    product_id: int
    predicted_price_7d: Optional[float] = None
    predicted_price_30d: Optional[float] = None
    confidence: float = 0
    price_direction: str = "stable"   # rising, falling, stable
    mape: Optional[float] = None


class TrendSignalData(BaseModel):
    """Signal extracted from the trend engine for decision-making."""
    product_id: int
    trend_score: float = 0
    trend_type: str = "stable"
    velocity_ratio: float = 0
    price_momentum: float = 0
    is_breakout: bool = False


class OpportunityContext(BaseModel):
    """Enriched opportunity context fed into the orchestrator."""
    opportunity_id: int
    product_id: int
    buy_price: float
    sell_price: float
    net_profit: float
    roi: float
    buy_marketplace: str
    sell_marketplace: str
    opportunity_score: float = 0
    confidence_score: float = 0
    risk_score: float = 50


class OrchestratorDecision(BaseModel):
    """Final decision produced by the orchestrator for an opportunity."""
    opportunity_id: int
    product_id: int
    decision: DecisionType
    signal_strength: SignalStrength
    score: float = Field(default=0, ge=0, le=100)
    prediction_signal: Optional[PredictionSignal] = None
    trend_signal: Optional[TrendSignalData] = None
    reasons: list[str] = Field(default_factory=list)
    recommended_action: Optional[str] = None    # buy, sell, monitor, skip
    recommended_price: Optional[float] = None
    metadata: dict = Field(default_factory=dict)


class IntelligencePipeline(BaseModel):
    """Tracks the full lifecycle of a single orchestration run for one opportunity."""
    pipeline_id: str
    opportunity: OpportunityContext
    status: PipelineStatus = PipelineStatus.QUEUED
    phases: dict[str, PhaseResult] = Field(default_factory=dict)
    decision: Optional[OrchestratorDecision] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    total_duration_ms: Optional[float] = None

    def get_phase(self, phase: PipelinePhase) -> PhaseResult:
        if phase.value not in self.phases:
            self.phases[phase.value] = PhaseResult(phase=phase)
        return self.phases[phase.value]

    def mark_phase_started(self, phase: PipelinePhase) -> PhaseResult:
        result = self.get_phase(phase)
        result.status = PhaseStatus.RUNNING
        result.started_at = datetime.now(timezone.utc)
        return result

    def mark_phase_completed(self, phase: PipelinePhase, data: dict) -> PhaseResult:
        result = self.get_phase(phase)
        result.status = PhaseStatus.COMPLETED
        result.completed_at = datetime.now(timezone.utc)
        if result.started_at:
            result.duration_ms = (result.completed_at - result.started_at).total_seconds() * 1000
        result.data = data
        return result

    def mark_phase_failed(self, phase: PipelinePhase, error: str) -> PhaseResult:
        result = self.get_phase(phase)
        result.status = PhaseStatus.FAILED
        result.completed_at = datetime.now(timezone.utc)
        if result.started_at:
            result.duration_ms = (result.completed_at - result.started_at).total_seconds() * 1000
        result.error = error
        result.retries += 1
        return result

    @property
    def is_terminal(self) -> bool:
        return self.status in (PipelineStatus.COMPLETED, PipelineStatus.FAILED, PipelineStatus.PARTIAL)


class PipelineConfig(BaseModel):
    """Configuration for orchestrator behavior."""
    max_retries: int = 2
    phase_timeout_seconds: float = 30.0
    enable_prediction: bool = True
    enable_trend: bool = True
    enable_execution: bool = True
    min_score_for_execution: float = 60.0
    min_confidence_for_execution: float = 0.5
    parallel_enrichment: bool = True    # run prediction + trend in parallel
