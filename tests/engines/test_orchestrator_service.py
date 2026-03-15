"""Tests for the orchestrator application layer — service and coordinator."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from engines.orchestrator.application.orchestrator_service import (
    OrchestratorService,
    PredictionAdapter,
    TrendAdapter,
)
from engines.orchestrator.application.pipeline_coordinator import PipelineCoordinator
from engines.orchestrator.domain.enums import (
    DecisionType,
    PhaseStatus,
    PipelinePhase,
    PipelineStatus,
)
from engines.orchestrator.domain.models import (
    IntelligencePipeline,
    OpportunityContext,
    PipelineConfig,
)
from engines.orchestrator.domain.policies import CompositeDecisionPolicy


def _opp(**kwargs) -> OpportunityContext:
    defaults = {
        "opportunity_id": 1,
        "product_id": 100,
        "buy_price": 50.0,
        "sell_price": 80.0,
        "net_profit": 15.0,
        "roi": 0.20,
        "buy_marketplace": "amazon_us",
        "sell_marketplace": "mercadolibre_mx",
        "opportunity_score": 65,
        "confidence_score": 0.7,
        "risk_score": 35,
    }
    defaults.update(kwargs)
    return OpportunityContext(**defaults)


# ─── PipelineCoordinator tests ──────────────────────────────────────

class TestPipelineCoordinator:
    def setup_method(self):
        self.config = PipelineConfig(max_retries=1, phase_timeout_seconds=5.0)
        self.coordinator = PipelineCoordinator(self.config)

    @pytest.mark.asyncio
    async def test_run_phase_success(self):
        pipeline = IntelligencePipeline(pipeline_id="test-1", opportunity=_opp())

        async def mock_executor(product_id: int) -> dict:
            return {"result": "ok", "product_id": product_id}

        result = await self.coordinator.run_phase(
            pipeline, PipelinePhase.PREDICTION, mock_executor, product_id=100
        )

        assert result.status == PhaseStatus.COMPLETED
        assert result.data["result"] == "ok"
        assert result.duration_ms is not None

    @pytest.mark.asyncio
    async def test_run_phase_retry_on_failure(self):
        pipeline = IntelligencePipeline(pipeline_id="test-2", opportunity=_opp())
        call_count = 0

        async def failing_executor(product_id: int) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("Transient error")
            return {"recovered": True}

        result = await self.coordinator.run_phase(
            pipeline, PipelinePhase.TREND, failing_executor, product_id=100
        )

        assert result.status == PhaseStatus.COMPLETED
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_run_phase_exhausts_retries(self):
        pipeline = IntelligencePipeline(pipeline_id="test-3", opportunity=_opp())

        async def always_fails(product_id: int) -> dict:
            raise RuntimeError("Permanent failure")

        result = await self.coordinator.run_phase(
            pipeline, PipelinePhase.PREDICTION, always_fails, product_id=100
        )

        assert result.status == PhaseStatus.FAILED
        assert result.retries == 2  # initial + 1 retry
        assert "Permanent failure" in (result.error or "")

    @pytest.mark.asyncio
    async def test_run_phase_timeout(self):
        config = PipelineConfig(max_retries=0, phase_timeout_seconds=0.1)
        coordinator = PipelineCoordinator(config)
        pipeline = IntelligencePipeline(pipeline_id="test-4", opportunity=_opp())

        async def slow_executor(product_id: int) -> dict:
            await asyncio.sleep(5)
            return {}

        result = await coordinator.run_phase(
            pipeline, PipelinePhase.PREDICTION, slow_executor, product_id=100
        )

        assert result.status == PhaseStatus.FAILED
        assert "timed out" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_run_parallel(self):
        pipeline = IntelligencePipeline(pipeline_id="test-5", opportunity=_opp())

        async def pred_exec(product_id: int) -> dict:
            return {"predicted_price": 85}

        async def trend_exec(product_id: int) -> dict:
            return {"trend_score": 70}

        results = await self.coordinator.run_parallel(pipeline, [
            (PipelinePhase.PREDICTION, pred_exec, {"product_id": 100}),
            (PipelinePhase.TREND, trend_exec, {"product_id": 100}),
        ])

        assert len(results) == 2
        assert all(r.status == PhaseStatus.COMPLETED for r in results)

    def test_should_skip_phase(self):
        config = PipelineConfig(enable_prediction=False, enable_trend=True)
        coordinator = PipelineCoordinator(config)

        assert coordinator.should_skip_phase(PipelinePhase.PREDICTION) is True
        assert coordinator.should_skip_phase(PipelinePhase.TREND) is False
        assert coordinator.should_skip_phase(PipelinePhase.DECISION) is False


# ─── OrchestratorService tests ──────────────────────────────────────

@pytest.fixture
def mock_prediction_adapter():
    adapter = AsyncMock(spec=PredictionAdapter)
    adapter.enrich = AsyncMock(return_value={
        "product_id": 100,
        "predictions": {
            "7d": {
                "predicted_price": 85.0,
                "confidence": 0.75,
                "mape": 5.2,
                "model_type": "baseline",
                "status": "computed",
            },
            "30d": {
                "predicted_price": 90.0,
                "confidence": 0.6,
                "mape": 8.1,
                "model_type": "baseline",
                "status": "computed",
            },
        },
    })
    return adapter


@pytest.fixture
def mock_trend_adapter():
    adapter = AsyncMock(spec=TrendAdapter)
    adapter.enrich = AsyncMock(return_value={
        "product_id": 100,
        "trend": {
            "trend_score": 65,
            "trend_type": "rising",
            "velocity_ratio": 2.1,
            "price_momentum": 0.03,
            "trend_strength": "moderate",
        },
    })
    return adapter


@pytest.fixture
def mock_repo():
    repo = AsyncMock()
    repo.save_pipeline = AsyncMock()
    repo.get_pipeline = AsyncMock(return_value=None)
    repo.get_recent_decisions = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_publisher():
    pub = AsyncMock()
    pub.publish = AsyncMock()
    return pub


@pytest.fixture
def service(mock_prediction_adapter, mock_trend_adapter, mock_repo, mock_publisher):
    config = PipelineConfig(max_retries=0, phase_timeout_seconds=10)
    coordinator = PipelineCoordinator(config)
    policy = CompositeDecisionPolicy()

    return OrchestratorService(
        prediction_adapter=mock_prediction_adapter,
        trend_adapter=mock_trend_adapter,
        decision_policy=policy,
        coordinator=coordinator,
        repository=mock_repo,
        publisher=mock_publisher,
        config=config,
    )


@pytest.mark.asyncio
async def test_process_opportunity_full_pipeline(service, mock_repo, mock_publisher):
    """Full pipeline should produce a decision and persist it."""
    opp = _opp(roi=0.25, profit=20)
    decision = await service.process_opportunity(opp)

    assert decision.opportunity_id == 1
    assert decision.product_id == 100
    assert decision.decision in list(DecisionType)
    assert 0 <= decision.score <= 100
    assert len(decision.reasons) >= 1

    mock_repo.save_pipeline.assert_called_once()
    # At least pipeline_completed event
    assert mock_publisher.publish.call_count >= 1


@pytest.mark.asyncio
async def test_process_opportunity_prediction_failure(
    mock_trend_adapter, mock_repo, mock_publisher
):
    """Pipeline should still produce a decision even if prediction fails."""
    failing_pred = AsyncMock()
    failing_pred.enrich = AsyncMock(side_effect=RuntimeError("model crash"))

    config = PipelineConfig(max_retries=0, phase_timeout_seconds=5)
    coordinator = PipelineCoordinator(config)
    policy = CompositeDecisionPolicy()

    svc = OrchestratorService(
        prediction_adapter=failing_pred,
        trend_adapter=mock_trend_adapter,
        decision_policy=policy,
        coordinator=coordinator,
        repository=mock_repo,
        publisher=mock_publisher,
        config=config,
    )

    decision = await svc.process_opportunity(_opp())

    # Should still produce a decision (partial pipeline)
    assert decision.decision in list(DecisionType)
    assert decision.prediction_signal is None  # prediction failed
    assert decision.trend_signal is not None    # trend succeeded


@pytest.mark.asyncio
async def test_process_opportunity_no_adapters(mock_repo, mock_publisher):
    """Pipeline with no adapters should still produce a profitability-based decision."""
    config = PipelineConfig(
        enable_prediction=False, enable_trend=False,
        max_retries=0, phase_timeout_seconds=5,
    )
    coordinator = PipelineCoordinator(config)
    policy = CompositeDecisionPolicy()

    svc = OrchestratorService(
        prediction_adapter=None,
        trend_adapter=None,
        decision_policy=policy,
        coordinator=coordinator,
        repository=mock_repo,
        publisher=mock_publisher,
        config=config,
    )

    decision = await svc.process_opportunity(_opp(roi=0.30, profit=20, risk=25))
    assert decision.decision in list(DecisionType)
    assert decision.prediction_signal is None
    assert decision.trend_signal is None


@pytest.mark.asyncio
async def test_process_batch(service):
    """Batch processing should handle multiple opportunities."""
    opps = [_opp(opportunity_id=i, product_id=i * 10) for i in range(1, 4)]
    decisions = await service.process_batch(opps)

    assert len(decisions) == 3
    assert all(d.decision in list(DecisionType) for d in decisions)


@pytest.mark.asyncio
async def test_process_batch_continues_on_error(mock_repo, mock_publisher):
    """Batch should continue if one opportunity fails."""
    call_count = 0

    async def flaky_enrich(product_id: int) -> dict:
        nonlocal call_count
        call_count += 1
        if product_id == 20:  # second product fails
            raise RuntimeError("bad product")
        return {"product_id": product_id, "predictions": {}}

    pred_adapter = AsyncMock()
    pred_adapter.enrich = flaky_enrich

    trend_adapter = AsyncMock()
    trend_adapter.enrich = AsyncMock(return_value={"product_id": 0, "trend": None})

    config = PipelineConfig(max_retries=0, phase_timeout_seconds=5)
    svc = OrchestratorService(
        prediction_adapter=pred_adapter,
        trend_adapter=trend_adapter,
        decision_policy=CompositeDecisionPolicy(),
        coordinator=PipelineCoordinator(config),
        repository=mock_repo,
        publisher=mock_publisher,
        config=config,
    )

    opps = [_opp(opportunity_id=i, product_id=i * 10) for i in range(1, 4)]
    decisions = await svc.process_batch(opps)

    # All 3 should still produce decisions (prediction failure is partial, not fatal)
    assert len(decisions) == 3


@pytest.mark.asyncio
async def test_execute_decision_publishes_recommendation(
    service, mock_publisher
):
    """Execute decisions should publish an execution_recommended event."""
    opp = _opp(roi=0.50, profit=40, risk=10)
    decision = await service.process_opportunity(opp)

    if decision.decision == DecisionType.EXECUTE:
        # Should have published both pipeline_completed and execution_recommended
        event_types = [
            call.args[0] for call in mock_publisher.publish.call_args_list
        ]
        assert "execution_recommended" in event_types


@pytest.mark.asyncio
async def test_signal_extraction_from_phases(service):
    """Verify prediction and trend signals are correctly extracted."""
    opp = _opp()
    decision = await service.process_opportunity(opp)

    # With our mock adapters, both signals should be present
    assert decision.prediction_signal is not None
    assert decision.prediction_signal.predicted_price_7d == 85.0
    assert decision.prediction_signal.confidence == 0.75

    assert decision.trend_signal is not None
    assert decision.trend_signal.trend_score == 65
    assert decision.trend_signal.trend_type == "rising"
