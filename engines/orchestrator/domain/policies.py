"""Decision policies determine the orchestrator's recommendation.

Each policy evaluates signals from the prediction and trend engines
against the opportunity context and returns a decision.

Policies are composable via the CompositePolicythat runs all
sub-policies and reconciles their outputs.
"""
import logging
from abc import ABC, abstractmethod
from typing import Optional

from .enums import DecisionType, SignalStrength
from .models import (
    OrchestratorDecision,
    OpportunityContext,
    PredictionSignal,
    TrendSignalData,
)

logger = logging.getLogger(__name__)


class DecisionPolicy(ABC):
    """Abstract base for decision policies."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    def weight(self) -> float:
        return 1.0

    @abstractmethod
    def evaluate(
        self,
        opportunity: OpportunityContext,
        prediction: Optional[PredictionSignal],
        trend: Optional[TrendSignalData],
    ) -> tuple[DecisionType, float, str]:
        """Returns (decision, score_contribution, reason)."""
        ...


class ProfitabilityPolicy(DecisionPolicy):
    """Evaluates whether the opportunity's profit metrics warrant action."""

    @property
    def name(self) -> str:
        return "profitability"

    @property
    def weight(self) -> float:
        return 0.30

    def evaluate(
        self,
        opportunity: OpportunityContext,
        prediction: Optional[PredictionSignal],
        trend: Optional[TrendSignalData],
    ) -> tuple[DecisionType, float, str]:
        roi = opportunity.roi
        profit = opportunity.net_profit

        if roi >= 0.30 and profit >= 20:
            return DecisionType.EXECUTE, 90, f"Strong profitability: ROI {roi:.0%}, profit ${profit:.2f}"
        if roi >= 0.15 and profit >= 10:
            return DecisionType.EXECUTE, 70, f"Good profitability: ROI {roi:.0%}, profit ${profit:.2f}"
        if roi >= 0.05 and profit >= 5:
            return DecisionType.MONITOR, 40, f"Marginal profitability: ROI {roi:.0%}, profit ${profit:.2f}"
        return DecisionType.SKIP, 10, f"Insufficient profitability: ROI {roi:.0%}, profit ${profit:.2f}"


class PredictionAlignmentPolicy(DecisionPolicy):
    """Evaluates whether price predictions support the opportunity."""

    @property
    def name(self) -> str:
        return "prediction_alignment"

    @property
    def weight(self) -> float:
        return 0.25

    def evaluate(
        self,
        opportunity: OpportunityContext,
        prediction: Optional[PredictionSignal],
        trend: Optional[TrendSignalData],
    ) -> tuple[DecisionType, float, str]:
        if prediction is None:
            return DecisionType.MONITOR, 30, "No prediction data available"

        if prediction.confidence < 0.3:
            return DecisionType.HOLD, 20, f"Low prediction confidence: {prediction.confidence:.2f}"

        # If sell price is predicted to rise → opportunity improves
        if prediction.price_direction == "rising" and prediction.confidence >= 0.6:
            return DecisionType.EXECUTE, 85, f"Price predicted to rise (conf={prediction.confidence:.2f})"

        # If sell price is predicted to fall → opportunity degrades
        if prediction.price_direction == "falling" and prediction.confidence >= 0.5:
            return DecisionType.SKIP, 15, f"Price predicted to fall (conf={prediction.confidence:.2f})"

        return DecisionType.MONITOR, 50, f"Stable price prediction (conf={prediction.confidence:.2f})"


class TrendMomentumPolicy(DecisionPolicy):
    """Evaluates whether market trends support the opportunity."""

    @property
    def name(self) -> str:
        return "trend_momentum"

    @property
    def weight(self) -> float:
        return 0.25

    def evaluate(
        self,
        opportunity: OpportunityContext,
        prediction: Optional[PredictionSignal],
        trend: Optional[TrendSignalData],
    ) -> tuple[DecisionType, float, str]:
        if trend is None:
            return DecisionType.MONITOR, 30, "No trend data available"

        if trend.is_breakout:
            return DecisionType.EXECUTE, 95, f"Breakout detected (score={trend.trend_score:.0f})"

        if trend.trend_type == "rising" and trend.trend_score >= 60:
            return DecisionType.EXECUTE, 75, f"Strong rising trend (score={trend.trend_score:.0f})"

        if trend.trend_type == "falling" and trend.trend_score >= 50:
            return DecisionType.SKIP, 15, f"Falling trend (score={trend.trend_score:.0f})"

        if trend.trend_score >= 40:
            return DecisionType.MONITOR, 50, f"Moderate trend (score={trend.trend_score:.0f})"

        return DecisionType.MONITOR, 30, f"Weak trend signal (score={trend.trend_score:.0f})"


class RiskAdjustmentPolicy(DecisionPolicy):
    """Adjusts decision based on the opportunity's risk profile."""

    @property
    def name(self) -> str:
        return "risk_adjustment"

    @property
    def weight(self) -> float:
        return 0.20

    def evaluate(
        self,
        opportunity: OpportunityContext,
        prediction: Optional[PredictionSignal],
        trend: Optional[TrendSignalData],
    ) -> tuple[DecisionType, float, str]:
        risk = opportunity.risk_score

        if risk <= 25:
            return DecisionType.EXECUTE, 85, f"Low risk ({risk:.0f}/100)"
        if risk <= 45:
            return DecisionType.EXECUTE, 65, f"Moderate risk ({risk:.0f}/100)"
        if risk <= 65:
            return DecisionType.MONITOR, 40, f"Elevated risk ({risk:.0f}/100)"
        return DecisionType.SKIP, 10, f"High risk ({risk:.0f}/100)"


class CompositeDecisionPolicy:
    """Aggregates multiple policies into a single weighted decision.

    Scoring:
    - Weighted average of all policy scores.
    - Decision is determined by score thresholds and signal agreement.
    """

    def __init__(self, policies: list[DecisionPolicy] | None = None) -> None:
        if policies is None:
            policies = [
                ProfitabilityPolicy(),
                PredictionAlignmentPolicy(),
                TrendMomentumPolicy(),
                RiskAdjustmentPolicy(),
            ]
        self._policies = policies

    def evaluate(
        self,
        opportunity: OpportunityContext,
        prediction: Optional[PredictionSignal],
        trend: Optional[TrendSignalData],
    ) -> OrchestratorDecision:
        total_weight = 0.0
        weighted_score = 0.0
        decisions: list[DecisionType] = []
        reasons: list[str] = []
        policy_detail: dict[str, dict] = {}

        for policy in self._policies:
            try:
                decision, score, reason = policy.evaluate(opportunity, prediction, trend)
                weighted_score += score * policy.weight
                total_weight += policy.weight
                decisions.append(decision)
                reasons.append(f"[{policy.name}] {reason}")
                policy_detail[policy.name] = {
                    "decision": decision.value,
                    "score": score,
                    "weight": policy.weight,
                }
            except Exception:
                logger.error("Policy %s failed", policy.name, exc_info=True)
                reasons.append(f"[{policy.name}] evaluation error")

        composite_score = weighted_score / total_weight if total_weight > 0 else 0

        # Determine final decision from score thresholds + agreement
        final_decision = self._reconcile(decisions, composite_score)
        signal_strength = self._classify_strength(decisions, composite_score)

        # Determine recommended action
        recommended_action = None
        if final_decision == DecisionType.EXECUTE:
            recommended_action = "buy"
        elif final_decision == DecisionType.MONITOR:
            recommended_action = "monitor"

        return OrchestratorDecision(
            opportunity_id=opportunity.opportunity_id,
            product_id=opportunity.product_id,
            decision=final_decision,
            signal_strength=signal_strength,
            score=round(composite_score, 1),
            prediction_signal=prediction,
            trend_signal=trend,
            reasons=reasons,
            recommended_action=recommended_action,
            recommended_price=opportunity.buy_price if recommended_action == "buy" else None,
            metadata={"policy_detail": policy_detail},
        )

    @staticmethod
    def _reconcile(decisions: list[DecisionType], score: float) -> DecisionType:
        """Determine final decision from policy votes and composite score."""
        if not decisions:
            return DecisionType.SKIP

        execute_count = sum(1 for d in decisions if d == DecisionType.EXECUTE)
        skip_count = sum(1 for d in decisions if d == DecisionType.SKIP)

        # Strong consensus
        if score >= 70 and execute_count >= len(decisions) // 2:
            return DecisionType.EXECUTE
        if score < 30 or skip_count > len(decisions) // 2:
            return DecisionType.SKIP
        if score >= 50:
            return DecisionType.MONITOR

        # Conflicting signals
        if execute_count > 0 and skip_count > 0:
            return DecisionType.HOLD

        return DecisionType.MONITOR

    @staticmethod
    def _classify_strength(
        decisions: list[DecisionType], score: float
    ) -> SignalStrength:
        unique = set(decisions)
        if len(unique) == 1 and score >= 70:
            return SignalStrength.STRONG
        if score >= 50:
            return SignalStrength.MODERATE
        if len(unique) >= 3:
            return SignalStrength.CONFLICTING
        return SignalStrength.WEAK


# Default factory
DEFAULT_POLICIES: list[type[DecisionPolicy]] = [
    ProfitabilityPolicy,
    PredictionAlignmentPolicy,
    TrendMomentumPolicy,
    RiskAdjustmentPolicy,
]
