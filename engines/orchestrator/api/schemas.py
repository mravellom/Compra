from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class OpportunityInput(BaseModel):
    """Input for triggering orchestration on a specific opportunity."""
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


class BatchOrchestrationRequest(BaseModel):
    """Input for batch orchestration."""
    opportunities: list[OpportunityInput] = Field(default_factory=list)
    limit: int = Field(20, ge=1, le=100, description="Max opportunities to process (if empty, auto-select top)")


class DecisionOut(BaseModel):
    opportunity_id: int
    product_id: int
    decision: str
    signal_strength: str
    score: float
    reasons: list[str] = Field(default_factory=list)
    recommended_action: Optional[str] = None
    recommended_price: Optional[float] = None
    metadata: dict = Field(default_factory=dict)


class PipelineOut(BaseModel):
    pipeline_id: str
    opportunity_id: int
    product_id: int
    status: str
    decision: Optional[str] = None
    decision_score: Optional[float] = None
    signal_strength: Optional[str] = None
    phases: dict = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    total_duration_ms: Optional[float] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class DecisionStatsOut(BaseModel):
    decisions_24h: list[dict] = Field(default_factory=list)


class OrchestratorStatusOut(BaseModel):
    recent_decisions: list[dict] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
