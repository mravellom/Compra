"""
Portfolio Optimizer API routes.

Endpoints:
  POST /optimizer/run     — Run optimizer on provided candidates (testing/manual)
  GET  /optimizer/metrics — Get optimizer metrics snapshot
"""
import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..domain.models import OpportunityCandidate, RejectionReason
from ..domain.optimizer import PortfolioOptimizer, ScoringWeights, DiversificationConfig
from ..domain.models import PortfolioState

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/optimizer", tags=["portfolio-optimizer"])


# ── Schemas ───────────────────────────────────────────────


class CandidateIn(BaseModel):
    opportunity_id: int
    product_id: int
    buy_marketplace: str
    sell_marketplace: str
    buy_price: float
    sell_price: float
    expected_profit: float
    confidence: float = 50.0
    risk_score: float = 50.0
    capital_required: float = 0.0
    velocity_score: float = 50.0
    score: float = 0.0
    signal_strength: str = "moderate"


class OptimizeRequest(BaseModel):
    candidates: list[CandidateIn]
    total_capital: float = 10000.0
    max_risk_per_trade: float = 70.0
    max_open_positions: int = 20
    active_positions: int = 0


class ApprovedOut(BaseModel):
    opportunity_id: int
    product_id: int
    expected_profit: float
    capital_required: float
    buy_marketplace: str
    sell_marketplace: str


class RejectedOut(BaseModel):
    opportunity_id: int
    reason: str


class OptimizeResponse(BaseModel):
    approved: list[ApprovedOut]
    rejected: list[RejectedOut]
    total_capital_allocated: float
    total_expected_profit: float
    approval_rate: float


# ── Endpoints ─────────────────────────────────────────────


@router.post("/run", response_model=OptimizeResponse)
async def run_optimizer(request: OptimizeRequest):
    """Run portfolio optimization on provided candidates."""
    candidates = [
        OpportunityCandidate(
            opportunity_id=c.opportunity_id,
            product_id=c.product_id,
            buy_marketplace=c.buy_marketplace,
            sell_marketplace=c.sell_marketplace,
            buy_price=c.buy_price,
            sell_price=c.sell_price,
            expected_profit=c.expected_profit,
            confidence=c.confidence,
            risk_score=c.risk_score,
            capital_required=c.capital_required or c.buy_price,
            velocity_score=c.velocity_score,
            score=c.score,
            signal_strength=c.signal_strength,
        )
        for c in request.candidates
    ]

    state = PortfolioState(
        total_capital=request.total_capital,
        available_capital=request.total_capital,
        max_risk_per_trade=request.max_risk_per_trade,
        max_open_positions=request.max_open_positions,
        active_positions=request.active_positions,
    )

    optimizer = PortfolioOptimizer()
    result = optimizer.optimize(candidates, state)

    return OptimizeResponse(
        approved=[
            ApprovedOut(
                opportunity_id=c.opportunity_id,
                product_id=c.product_id,
                expected_profit=c.expected_profit,
                capital_required=c.capital_required,
                buy_marketplace=c.buy_marketplace,
                sell_marketplace=c.sell_marketplace,
            )
            for c in result.approved
        ],
        rejected=[
            RejectedOut(
                opportunity_id=r.candidate.opportunity_id,
                reason=r.reason.value,
            )
            for r in result.rejected
        ],
        total_capital_allocated=result.total_capital_allocated,
        total_expected_profit=result.total_expected_profit,
        approval_rate=result.approval_rate,
    )
