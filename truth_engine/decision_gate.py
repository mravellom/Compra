"""
Decision Gate — final score computation before execution.

Computes a composite final score from:
- expected_value (profit * probability_of_sale)
- confidence (0-100 normalized to 0-1)
- liquidity_score (0-100 normalized to 0-1)
- risk_score (0-100, inverted: lower risk = better)

Only opportunities above threshold are approved for execution.
"""
import logging
from dataclasses import field

from .config import TruthEngineConfig
from .models import DecisionInput, DecisionResult

logger = logging.getLogger(__name__)


class DecisionGate:
    """Final execution gate — the last filter before real money is committed."""

    def __init__(self, config: TruthEngineConfig | None = None) -> None:
        self._cfg = config or TruthEngineConfig()

    def evaluate(self, decision_input: DecisionInput) -> DecisionResult:
        """Compute final score and decide whether to execute.

        final_score = expected_value * confidence * liquidity * (1 - risk)

        Where:
        - expected_value = adjusted_profit * probability_of_sale
        - confidence, liquidity, risk are normalized to 0-1

        Rejects if:
        - final_score < min_final_score
        - expected_value < min_expected_value_usd
        """
        di = decision_input
        reasons: list[str] = []

        # Normalize scores to 0-1
        confidence_n = max(0.0, min(1.0, di.confidence_score / 100.0))
        liquidity_n = max(0.0, min(1.0, di.liquidity_score / 100.0))
        risk_n = max(0.0, min(1.0, di.risk_score / 100.0))

        # Final composite score
        final_score = (
            di.expected_value
            * confidence_n
            * liquidity_n
            * (1.0 - risk_n)
        )

        # Rejection checks
        if di.expected_value < self._cfg.min_expected_value_usd:
            reasons.append(
                f"expected_value ${di.expected_value:.2f} < ${self._cfg.min_expected_value_usd:.2f}"
            )

        if final_score < self._cfg.min_final_score:
            reasons.append(
                f"final_score {final_score:.4f} < {self._cfg.min_final_score}"
            )

        execute = len(reasons) == 0

        logger.info(
            "Decision: opp=%d score=%.4f execute=%s "
            "(EV=$%.2f conf=%.2f liq=%.2f risk=%.2f p_sale=%.2f) %s",
            di.opportunity_id, final_score, execute,
            di.expected_value, confidence_n, liquidity_n, risk_n,
            di.probability_of_sale,
            f"REJECT: {'; '.join(reasons)}" if reasons else "APPROVED",
        )

        return DecisionResult(
            opportunity_id=di.opportunity_id,
            final_score=round(final_score, 4),
            execute=execute,
            rejection_reasons=reasons,
            inputs=di,
        )

    def compute_expected_value(
        self,
        adjusted_profit: float,
        probability_of_sale: float,
    ) -> float:
        """EV = adjusted_profit * probability_of_sale."""
        return round(adjusted_profit * probability_of_sale, 2)

    def estimate_probability_of_sale(
        self,
        confidence_score: float,
        liquidity_score: float,
        historical_precision: float,
    ) -> float:
        """Estimate P(sale) from available signals.

        Combines:
        - confidence_score (model's own assessment, 0-100)
        - liquidity_score (market demand signal, 0-100)
        - historical_precision (actual success rate from truth engine, 0-1)

        Weights: historical > confidence > liquidity.
        """
        conf_n = max(0.0, min(1.0, confidence_score / 100.0))
        liq_n = max(0.0, min(1.0, liquidity_score / 100.0))
        hist = max(0.0, min(1.0, historical_precision))

        # If we have historical data, weight it heavily
        if hist > 0:
            p_sale = hist * 0.50 + conf_n * 0.30 + liq_n * 0.20
        else:
            # No history yet — rely on model signals
            p_sale = conf_n * 0.60 + liq_n * 0.40

        return round(max(0.01, min(0.99, p_sale)), 4)
