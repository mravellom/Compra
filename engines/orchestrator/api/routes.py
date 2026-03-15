"""Market Intelligence Orchestrator API routes."""
import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.database import async_session
from ..application.orchestrator_service import (
    OrchestratorService,
    PredictionAdapter,
    TrendAdapter,
)
from ..application.pipeline_coordinator import PipelineCoordinator
from ..domain.models import OpportunityContext, PipelineConfig
from ..domain.policies import CompositeDecisionPolicy
from ..infrastructure.orchestrator_repository import OrchestratorRepository
from ..infrastructure.redis_publisher import OrchestratorEventPublisher
from .schemas import (
    BatchOrchestrationRequest,
    DecisionOut,
    DecisionStatsOut,
    OpportunityInput,
    OrchestratorStatusOut,
    PipelineOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])


def _get_service() -> OrchestratorService:
    """Factory for OrchestratorService with all dependencies."""
    from engines.prediction.application.model_registry import create_default_registry
    from engines.prediction.application.prediction_service import PredictionService
    from engines.prediction.infrastructure.prediction_repository import PredictionRepository
    from engines.trend.application.trend_aggregator import TrendAggregator
    from engines.trend.application.trend_service import TrendService
    from engines.trend.domain.strategies import PriceMomentumStrategy, VelocitySpikeStrategy
    from engines.trend.infrastructure.trend_repository import TrendRepository

    config = PipelineConfig()

    # Prediction adapter
    pred_repo = PredictionRepository(async_session)
    pred_registry = create_default_registry()
    pred_service = PredictionService(pred_repo, pred_registry)
    prediction_adapter = PredictionAdapter(pred_service)

    # Trend adapter
    trend_repo = TrendRepository(async_session)
    trend_agg = TrendAggregator([VelocitySpikeStrategy(), PriceMomentumStrategy()])
    trend_service = TrendService(trend_repo, trend_agg)
    trend_adapter = TrendAdapter(trend_service)

    # Orchestrator components
    policy = CompositeDecisionPolicy()
    coordinator = PipelineCoordinator(config)
    repository = OrchestratorRepository(async_session)

    publisher = None
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            import redis.asyncio as aioredis
            client = aioredis.from_url(redis_url, decode_responses=True)
            publisher = OrchestratorEventPublisher(client)
        except Exception:
            logger.warning("Redis unavailable for orchestrator events")

    return OrchestratorService(
        prediction_adapter=prediction_adapter,
        trend_adapter=trend_adapter,
        decision_policy=policy,
        coordinator=coordinator,
        repository=repository,
        publisher=publisher,
        config=config,
    )


@router.post("/process", response_model=DecisionOut)
async def process_opportunity(request: OpportunityInput):
    """Run the full intelligence pipeline for a single opportunity."""
    service = _get_service()
    context = OpportunityContext(**request.model_dump())
    decision = await service.process_opportunity(context)
    return DecisionOut(
        opportunity_id=decision.opportunity_id,
        product_id=decision.product_id,
        decision=decision.decision.value,
        signal_strength=decision.signal_strength.value,
        score=decision.score,
        reasons=decision.reasons,
        recommended_action=decision.recommended_action,
        recommended_price=decision.recommended_price,
        metadata=decision.metadata,
    )


@router.post("/process/batch", response_model=list[DecisionOut])
async def process_batch(request: BatchOrchestrationRequest):
    """Run the intelligence pipeline for multiple opportunities."""
    service = _get_service()

    if not request.opportunities:
        # Auto-select top opportunities from DB
        from api.database import async_session as db_session
        from sqlalchemy import text

        async with db_session() as session:
            result = await session.execute(
                text("""
                    SELECT id, master_product_id, buy_price, sell_price,
                           net_profit, roi, buy_marketplace, sell_marketplace,
                           opportunity_score, confidence_score, risk_score
                    FROM opportunities
                    WHERE status = 'active'
                    ORDER BY opportunity_score DESC
                    LIMIT :limit
                """),
                {"limit": request.limit},
            )
            rows = result.fetchall()
            contexts = [
                OpportunityContext(
                    opportunity_id=r[0], product_id=r[1],
                    buy_price=float(r[2]), sell_price=float(r[3]),
                    net_profit=float(r[4]), roi=float(r[5]),
                    buy_marketplace=r[6], sell_marketplace=r[7],
                    opportunity_score=float(r[8] or 0),
                    confidence_score=float(r[9] or 0),
                    risk_score=float(r[10] or 50),
                )
                for r in rows
            ]
    else:
        contexts = [OpportunityContext(**o.model_dump()) for o in request.opportunities]

    decisions = await service.process_batch(contexts)
    return [
        DecisionOut(
            opportunity_id=d.opportunity_id,
            product_id=d.product_id,
            decision=d.decision.value,
            signal_strength=d.signal_strength.value,
            score=d.score,
            reasons=d.reasons,
            recommended_action=d.recommended_action,
            recommended_price=d.recommended_price,
            metadata=d.metadata,
        )
        for d in decisions
    ]


@router.get("/pipelines/{pipeline_id}", response_model=PipelineOut)
async def get_pipeline(pipeline_id: str):
    """Get detailed pipeline execution record."""
    repo = OrchestratorRepository(async_session)
    pipeline = await repo.get_pipeline(pipeline_id)
    if pipeline is None:
        raise HTTPException(404, f"Pipeline {pipeline_id} not found")
    return PipelineOut(**pipeline)


@router.get("/decisions", response_model=list[dict])
async def list_recent_decisions(limit: int = Query(20, ge=1, le=100)):
    """List recent orchestrator decisions."""
    repo = OrchestratorRepository(async_session)
    return await repo.get_recent_decisions(limit)


@router.get("/stats", response_model=DecisionStatsOut)
async def decision_stats():
    """Get decision distribution stats for the last 24 hours."""
    repo = OrchestratorRepository(async_session)
    return DecisionStatsOut(**(await repo.get_decision_stats()))
