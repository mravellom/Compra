"""Market Intelligence Orchestrator — the central coordinator.

Orchestrates the prediction, trend, and execution engines for each
opportunity, producing an actionable decision.

Pipeline flow:
1. Receive opportunity context
2. Run prediction + trend engines in parallel (enrichment phase)
3. Extract signals from enrichment results
4. Evaluate decision policies
5. Optionally create execution order if decision = EXECUTE
6. Publish orchestrator events
7. Store pipeline result
"""
import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from ..domain.enums import (
    DecisionType,
    PhaseStatus,
    PipelinePhase,
    PipelineStatus,
)
from ..domain.models import (
    IntelligencePipeline,
    OpportunityContext,
    OrchestratorDecision,
    PipelineConfig,
    PredictionSignal,
    TrendSignalData,
)
from ..domain.policies import CompositeDecisionPolicy
from ..infrastructure.orchestrator_repository import OrchestratorRepository
from ..infrastructure.redis_publisher import OrchestratorEventPublisher
from .pipeline_coordinator import PipelineCoordinator

logger = logging.getLogger(__name__)


class PredictionAdapter:
    """Adapts the PredictionService for use by the orchestrator.

    Decouples the orchestrator from the prediction engine's internals.
    """

    def __init__(self, prediction_service) -> None:
        self._service = prediction_service

    async def enrich(self, product_id: int) -> dict:
        """Run predictions and return signal data."""
        from engines.prediction.domain.enums import ForecastHorizon
        from engines.prediction.domain.models import PredictionRequest

        results: dict = {"product_id": product_id, "predictions": {}}

        for horizon in [ForecastHorizon.DAYS_7, ForecastHorizon.DAYS_30]:
            request = PredictionRequest(product_id=product_id, horizon=horizon)
            forecast = await self._service.predict(request)
            results["predictions"][f"{horizon.value}d"] = {
                "predicted_price": forecast.predicted_price,
                "confidence": forecast.confidence,
                "mape": forecast.mape,
                "model_type": forecast.model_type,
                "status": forecast.status,
            }

        return results


class TrendAdapter:
    """Adapts the TrendService for use by the orchestrator."""

    def __init__(self, trend_service) -> None:
        self._service = trend_service

    async def enrich(self, product_id: int) -> dict:
        """Detect trend and return signal data."""
        # First try to get existing trend
        trend = await self._service.get_trend(product_id)

        if trend is None:
            # Trigger fresh detection
            results = await self._service.detect_trends(limit=1)
            # The detect_trends runs on active products; try fetching again
            trend = await self._service.get_trend(product_id)

        if trend is None:
            return {"product_id": product_id, "trend": None}

        return {
            "product_id": product_id,
            "trend": {
                "trend_score": trend.trend_score,
                "trend_type": trend.trend_type,
                "velocity_ratio": trend.velocity_ratio,
                "price_momentum": trend.price_momentum,
                "trend_strength": trend.trend_strength,
            },
        }


class OrchestratorService:
    """Main orchestrator service. Coordinates all engines for each opportunity."""

    def __init__(
        self,
        prediction_adapter: Optional[PredictionAdapter],
        trend_adapter: Optional[TrendAdapter],
        decision_policy: CompositeDecisionPolicy,
        coordinator: PipelineCoordinator,
        repository: OrchestratorRepository,
        publisher: Optional[OrchestratorEventPublisher] = None,
        config: Optional[PipelineConfig] = None,
    ) -> None:
        self._prediction = prediction_adapter
        self._trend = trend_adapter
        self._policy = decision_policy
        self._coordinator = coordinator
        self._repo = repository
        self._publisher = publisher
        self._config = config or PipelineConfig()

    async def process_opportunity(
        self, opportunity: OpportunityContext
    ) -> OrchestratorDecision:
        """Run the full intelligence pipeline for a single opportunity."""
        pipeline_id = f"pipe-{opportunity.opportunity_id}-{uuid.uuid4().hex[:8]}"
        pipeline = IntelligencePipeline(
            pipeline_id=pipeline_id,
            opportunity=opportunity,
            status=PipelineStatus.RUNNING,
        )

        logger.info(
            "Pipeline %s started for opportunity %d (product %d, ROI %.0f%%)",
            pipeline_id, opportunity.opportunity_id,
            opportunity.product_id, opportunity.roi * 100,
        )

        start_time = time.monotonic()

        # Phase 1+2: Enrichment (prediction + trend in parallel)
        prediction_signal = None
        trend_signal = None

        enrichment_phases = []
        if self._prediction and not self._coordinator.should_skip_phase(PipelinePhase.PREDICTION):
            enrichment_phases.append((
                PipelinePhase.PREDICTION,
                self._prediction.enrich,
                {"product_id": opportunity.product_id},
            ))
        if self._trend and not self._coordinator.should_skip_phase(PipelinePhase.TREND):
            enrichment_phases.append((
                PipelinePhase.TREND,
                self._trend.enrich,
                {"product_id": opportunity.product_id},
            ))

        if enrichment_phases:
            if self._config.parallel_enrichment and len(enrichment_phases) > 1:
                await self._coordinator.run_parallel(pipeline, enrichment_phases)
            else:
                for phase, executor, kwargs in enrichment_phases:
                    await self._coordinator.run_phase(pipeline, phase, executor, **kwargs)

        # Extract signals from completed phases
        prediction_signal = self._extract_prediction_signal(pipeline, opportunity.product_id)
        trend_signal = self._extract_trend_signal(pipeline, opportunity.product_id)

        # Phase 3: Decision
        decision = self._policy.evaluate(opportunity, prediction_signal, trend_signal)
        pipeline.mark_phase_started(PipelinePhase.DECISION)
        pipeline.mark_phase_completed(PipelinePhase.DECISION, {
            "decision": decision.decision.value,
            "score": decision.score,
            "signal_strength": decision.signal_strength.value,
        })
        pipeline.decision = decision

        # Finalize pipeline
        elapsed_ms = (time.monotonic() - start_time) * 1000
        pipeline.total_duration_ms = round(elapsed_ms, 1)
        pipeline.completed_at = datetime.now(timezone.utc)

        # Determine pipeline status
        failed_phases = [
            p for p in pipeline.phases.values()
            if p.status == PhaseStatus.FAILED
        ]
        if not failed_phases:
            pipeline.status = PipelineStatus.COMPLETED
        elif pipeline.decision is not None:
            pipeline.status = PipelineStatus.PARTIAL
        else:
            pipeline.status = PipelineStatus.FAILED

        # Persist pipeline result
        await self._repo.save_pipeline(pipeline)

        # Publish events
        if self._publisher:
            await self._publisher.publish("pipeline_completed", {
                "pipeline_id": pipeline_id,
                "opportunity_id": opportunity.opportunity_id,
                "product_id": opportunity.product_id,
                "decision": decision.decision.value,
                "score": decision.score,
                "signal_strength": decision.signal_strength.value,
                "duration_ms": pipeline.total_duration_ms,
            })

            if decision.decision == DecisionType.EXECUTE:
                await self._publisher.publish("execution_recommended", {
                    "pipeline_id": pipeline_id,
                    "opportunity_id": opportunity.opportunity_id,
                    "product_id": opportunity.product_id,
                    "recommended_action": decision.recommended_action,
                    "recommended_price": decision.recommended_price,
                    "score": decision.score,
                })

        logger.info(
            "Pipeline %s completed: decision=%s score=%.0f strength=%s (%.0fms)",
            pipeline_id, decision.decision.value, decision.score,
            decision.signal_strength.value, elapsed_ms,
        )

        return decision

    async def process_batch(
        self, opportunities: list[OpportunityContext]
    ) -> list[OrchestratorDecision]:
        """Process multiple opportunities sequentially."""
        sem = asyncio.Semaphore(5)
        async def _process_one(opp):
            async with sem:
                try:
                    return await self.process_opportunity(opp)
                except Exception as e:
                    logger.error("Orchestrator failed for opp %d: %s", opp.opportunity_id, e)
                    return None

        raw_results = await asyncio.gather(*[_process_one(opp) for opp in opportunities])
        decisions = [r for r in raw_results if r is not None]
        return decisions

    async def get_pipeline(self, pipeline_id: str) -> Optional[IntelligencePipeline]:
        return await self._repo.get_pipeline(pipeline_id)

    async def get_recent_decisions(
        self, limit: int = 20
    ) -> list[OrchestratorDecision]:
        return await self._repo.get_recent_decisions(limit)

    def _extract_prediction_signal(
        self, pipeline: IntelligencePipeline, product_id: int
    ) -> Optional[PredictionSignal]:
        """Extract a PredictionSignal from the prediction phase result."""
        phase = pipeline.phases.get(PipelinePhase.PREDICTION.value)
        if phase is None or phase.status != PhaseStatus.COMPLETED:
            return None

        predictions = phase.data.get("predictions", {})
        pred_7d = predictions.get("7d", {})
        pred_30d = predictions.get("30d", {})

        if not pred_7d:
            return None

        # Determine price direction from 7d vs current sell price
        predicted_7d = pred_7d.get("predicted_price", 0)
        confidence = pred_7d.get("confidence", 0)
        current_price = pipeline.opportunity.sell_price

        if predicted_7d > 0 and current_price > 0:
            change_pct = (predicted_7d - current_price) / current_price
            if change_pct > 0.02:
                direction = "rising"
            elif change_pct < -0.02:
                direction = "falling"
            else:
                direction = "stable"
        else:
            direction = "stable"

        return PredictionSignal(
            product_id=product_id,
            predicted_price_7d=predicted_7d,
            predicted_price_30d=pred_30d.get("predicted_price"),
            confidence=confidence,
            price_direction=direction,
            mape=pred_7d.get("mape"),
        )

    def _extract_trend_signal(
        self, pipeline: IntelligencePipeline, product_id: int
    ) -> Optional[TrendSignalData]:
        """Extract a TrendSignalData from the trend phase result."""
        phase = pipeline.phases.get(PipelinePhase.TREND.value)
        if phase is None or phase.status != PhaseStatus.COMPLETED:
            return None

        trend = phase.data.get("trend")
        if trend is None:
            return None

        return TrendSignalData(
            product_id=product_id,
            trend_score=trend.get("trend_score", 0),
            trend_type=trend.get("trend_type", "stable"),
            velocity_ratio=trend.get("velocity_ratio", 0),
            price_momentum=trend.get("price_momentum", 0),
            is_breakout=trend.get("trend_type") == "breakout",
        )
