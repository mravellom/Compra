"""Tests for the orchestrator domain layer — policies, models, enums."""
import pytest
from datetime import datetime, timezone

from engines.orchestrator.domain.enums import (
    DecisionType,
    PhaseStatus,
    PipelinePhase,
    PipelineStatus,
    SignalStrength,
)
from engines.orchestrator.domain.models import (
    IntelligencePipeline,
    OpportunityContext,
    OrchestratorDecision,
    PhaseResult,
    PipelineConfig,
    PredictionSignal,
    TrendSignalData,
)
from engines.orchestrator.domain.policies import (
    CompositeDecisionPolicy,
    PredictionAlignmentPolicy,
    ProfitabilityPolicy,
    RiskAdjustmentPolicy,
    TrendMomentumPolicy,
)


# ─── Test helpers ────────────────────────────────────────────────────

def _opp(
    roi: float = 0.20,
    profit: float = 15.0,
    score: float = 60,
    confidence: float = 0.6,
    risk: float = 40,
) -> OpportunityContext:
    return OpportunityContext(
        opportunity_id=1,
        product_id=100,
        buy_price=50.0,
        sell_price=80.0,
        net_profit=profit,
        roi=roi,
        buy_marketplace="amazon_us",
        sell_marketplace="mercadolibre_mx",
        opportunity_score=score,
        confidence_score=confidence,
        risk_score=risk,
    )


def _pred(
    direction: str = "rising",
    confidence: float = 0.7,
    price_7d: float = 85.0,
) -> PredictionSignal:
    return PredictionSignal(
        product_id=100,
        predicted_price_7d=price_7d,
        confidence=confidence,
        price_direction=direction,
    )


def _trend(
    score: float = 65,
    trend_type: str = "rising",
    breakout: bool = False,
) -> TrendSignalData:
    return TrendSignalData(
        product_id=100,
        trend_score=score,
        trend_type=trend_type,
        velocity_ratio=2.0,
        price_momentum=0.03,
        is_breakout=breakout,
    )


# ─── Pipeline model tests ───────────────────────────────────────────

class TestIntelligencePipeline:
    def test_creation(self):
        pipeline = IntelligencePipeline(
            pipeline_id="test-1",
            opportunity=_opp(),
        )
        assert pipeline.status == PipelineStatus.QUEUED
        assert pipeline.decision is None
        assert not pipeline.is_terminal

    def test_mark_phase_lifecycle(self):
        pipeline = IntelligencePipeline(pipeline_id="test-2", opportunity=_opp())

        # Start
        result = pipeline.mark_phase_started(PipelinePhase.PREDICTION)
        assert result.status == PhaseStatus.RUNNING
        assert result.started_at is not None

        # Complete
        result = pipeline.mark_phase_completed(
            PipelinePhase.PREDICTION, {"predicted_price": 85.0}
        )
        assert result.status == PhaseStatus.COMPLETED
        assert result.data["predicted_price"] == 85.0
        assert result.duration_ms is not None
        assert result.duration_ms >= 0

    def test_mark_phase_failed(self):
        pipeline = IntelligencePipeline(pipeline_id="test-3", opportunity=_opp())
        pipeline.mark_phase_started(PipelinePhase.TREND)
        result = pipeline.mark_phase_failed(PipelinePhase.TREND, "timeout")
        assert result.status == PhaseStatus.FAILED
        assert result.error == "timeout"
        assert result.retries == 1

    def test_is_terminal(self):
        pipeline = IntelligencePipeline(pipeline_id="test-4", opportunity=_opp())
        assert not pipeline.is_terminal

        pipeline.status = PipelineStatus.COMPLETED
        assert pipeline.is_terminal

        pipeline.status = PipelineStatus.FAILED
        assert pipeline.is_terminal

    def test_get_phase_creates_if_missing(self):
        pipeline = IntelligencePipeline(pipeline_id="test-5", opportunity=_opp())
        result = pipeline.get_phase(PipelinePhase.DECISION)
        assert result.phase == PipelinePhase.DECISION
        assert result.status == PhaseStatus.PENDING


class TestPipelineConfig:
    def test_defaults(self):
        config = PipelineConfig()
        assert config.max_retries == 2
        assert config.parallel_enrichment is True
        assert config.min_score_for_execution == 60.0


# ─── Individual policy tests ────────────────────────────────────────

class TestProfitabilityPolicy:
    def setup_method(self):
        self.policy = ProfitabilityPolicy()

    def test_strong_profitability(self):
        decision, score, reason = self.policy.evaluate(_opp(roi=0.35, profit=25), None, None)
        assert decision == DecisionType.EXECUTE
        assert score >= 80

    def test_good_profitability(self):
        decision, score, _ = self.policy.evaluate(_opp(roi=0.20, profit=12), None, None)
        assert decision == DecisionType.EXECUTE
        assert score >= 60

    def test_marginal_profitability(self):
        decision, score, _ = self.policy.evaluate(_opp(roi=0.08, profit=6), None, None)
        assert decision == DecisionType.MONITOR

    def test_insufficient_profitability(self):
        decision, score, _ = self.policy.evaluate(_opp(roi=0.02, profit=2), None, None)
        assert decision == DecisionType.SKIP
        assert score <= 20

    def test_weight(self):
        assert self.policy.weight == 0.30


class TestPredictionAlignmentPolicy:
    def setup_method(self):
        self.policy = PredictionAlignmentPolicy()

    def test_no_prediction(self):
        decision, _, _ = self.policy.evaluate(_opp(), None, None)
        assert decision == DecisionType.MONITOR

    def test_rising_high_confidence(self):
        decision, score, _ = self.policy.evaluate(
            _opp(), _pred(direction="rising", confidence=0.8), None
        )
        assert decision == DecisionType.EXECUTE
        assert score >= 80

    def test_falling_prediction(self):
        decision, _, _ = self.policy.evaluate(
            _opp(), _pred(direction="falling", confidence=0.7), None
        )
        assert decision == DecisionType.SKIP

    def test_low_confidence(self):
        decision, _, _ = self.policy.evaluate(
            _opp(), _pred(confidence=0.2), None
        )
        assert decision == DecisionType.HOLD

    def test_stable_prediction(self):
        decision, _, _ = self.policy.evaluate(
            _opp(), _pred(direction="stable", confidence=0.6), None
        )
        assert decision == DecisionType.MONITOR


class TestTrendMomentumPolicy:
    def setup_method(self):
        self.policy = TrendMomentumPolicy()

    def test_no_trend(self):
        decision, _, _ = self.policy.evaluate(_opp(), None, None)
        assert decision == DecisionType.MONITOR

    def test_breakout(self):
        decision, score, _ = self.policy.evaluate(
            _opp(), None, _trend(breakout=True)
        )
        assert decision == DecisionType.EXECUTE
        assert score >= 90

    def test_strong_rising(self):
        decision, _, _ = self.policy.evaluate(
            _opp(), None, _trend(score=70, trend_type="rising")
        )
        assert decision == DecisionType.EXECUTE

    def test_falling_trend(self):
        decision, _, _ = self.policy.evaluate(
            _opp(), None, _trend(score=60, trend_type="falling")
        )
        assert decision == DecisionType.SKIP

    def test_weak_trend(self):
        decision, _, _ = self.policy.evaluate(
            _opp(), None, _trend(score=25, trend_type="stable")
        )
        assert decision == DecisionType.MONITOR


class TestRiskAdjustmentPolicy:
    def setup_method(self):
        self.policy = RiskAdjustmentPolicy()

    def test_low_risk(self):
        decision, score, _ = self.policy.evaluate(_opp(risk=20), None, None)
        assert decision == DecisionType.EXECUTE
        assert score >= 80

    def test_moderate_risk(self):
        decision, _, _ = self.policy.evaluate(_opp(risk=40), None, None)
        assert decision == DecisionType.EXECUTE

    def test_elevated_risk(self):
        decision, _, _ = self.policy.evaluate(_opp(risk=55), None, None)
        assert decision == DecisionType.MONITOR

    def test_high_risk(self):
        decision, score, _ = self.policy.evaluate(_opp(risk=80), None, None)
        assert decision == DecisionType.SKIP
        assert score <= 15


# ─── Composite policy tests ─────────────────────────────────────────

class TestCompositeDecisionPolicy:
    def setup_method(self):
        self.composite = CompositeDecisionPolicy()

    def test_all_positive_signals(self):
        """Strong profit + rising prediction + breakout trend + low risk → EXECUTE."""
        decision = self.composite.evaluate(
            _opp(roi=0.35, profit=25, risk=20),
            _pred(direction="rising", confidence=0.8),
            _trend(score=80, breakout=True),
        )
        assert decision.decision == DecisionType.EXECUTE
        assert decision.signal_strength in (SignalStrength.STRONG, SignalStrength.MODERATE)
        assert decision.score >= 70
        assert len(decision.reasons) == 4

    def test_all_negative_signals(self):
        """Low profit + falling prediction + falling trend + high risk → SKIP."""
        decision = self.composite.evaluate(
            _opp(roi=0.02, profit=2, risk=80),
            _pred(direction="falling", confidence=0.7),
            _trend(score=60, trend_type="falling"),
        )
        assert decision.decision == DecisionType.SKIP
        assert decision.score < 30

    def test_mixed_signals(self):
        """Good profit but falling trend → MONITOR or HOLD."""
        decision = self.composite.evaluate(
            _opp(roi=0.25, profit=20, risk=30),
            _pred(direction="stable", confidence=0.5),
            _trend(score=55, trend_type="falling"),
        )
        assert decision.decision in (DecisionType.MONITOR, DecisionType.HOLD, DecisionType.SKIP)

    def test_no_enrichment_data(self):
        """Without prediction/trend data, decision depends on profitability + risk."""
        decision = self.composite.evaluate(
            _opp(roi=0.30, profit=20, risk=30),
            None,
            None,
        )
        assert decision.decision in (DecisionType.EXECUTE, DecisionType.MONITOR)
        assert decision.score > 30

    def test_metadata_contains_policy_detail(self):
        decision = self.composite.evaluate(_opp(), _pred(), _trend())
        assert "policy_detail" in decision.metadata
        detail = decision.metadata["policy_detail"]
        assert "profitability" in detail
        assert "prediction_alignment" in detail
        assert "trend_momentum" in detail
        assert "risk_adjustment" in detail

    def test_recommended_action_set_for_execute(self):
        decision = self.composite.evaluate(
            _opp(roi=0.40, profit=30, risk=15),
            _pred(direction="rising", confidence=0.9),
            _trend(score=85, breakout=True),
        )
        if decision.decision == DecisionType.EXECUTE:
            assert decision.recommended_action == "buy"
            assert decision.recommended_price is not None

    def test_signal_strength_classification(self):
        # Strong: all agree + high score
        decision = self.composite.evaluate(
            _opp(roi=0.50, profit=40, risk=10),
            _pred(direction="rising", confidence=0.9),
            _trend(score=90, breakout=True),
        )
        assert decision.signal_strength in (SignalStrength.STRONG, SignalStrength.MODERATE)
