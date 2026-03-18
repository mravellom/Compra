"""
Tests — Decision Gate: final score computation and execution approval.
"""
import pytest

from truth_engine.config import TruthEngineConfig
from truth_engine.decision_gate import DecisionGate
from truth_engine.models import DecisionInput


def _input(
    expected_value: float = 25.0,
    confidence_score: float = 80.0,
    liquidity_score: float = 70.0,
    risk_score: float = 30.0,
    probability_of_sale: float = 0.65,
    adjusted_profit: float = 40.0,
    estimated_time_to_sell_days: float = 3.0,
    opportunity_id: int = 1,
) -> DecisionInput:
    return DecisionInput(
        opportunity_id=opportunity_id,
        expected_value=expected_value,
        confidence_score=confidence_score,
        liquidity_score=liquidity_score,
        risk_score=risk_score,
        probability_of_sale=probability_of_sale,
        adjusted_profit=adjusted_profit,
        estimated_time_to_sell_days=estimated_time_to_sell_days,
    )


class TestDecisionGate:

    def test_approve_good_opportunity(self):
        gate = DecisionGate()
        result = gate.evaluate(_input(
            expected_value=30.0,
            confidence_score=85.0,
            liquidity_score=75.0,
            risk_score=20.0,
        ))
        assert result.execute is True
        assert result.final_score > 0
        assert len(result.rejection_reasons) == 0

    def test_reject_low_expected_value(self):
        cfg = TruthEngineConfig(min_expected_value_usd=15.0)
        gate = DecisionGate(config=cfg)
        result = gate.evaluate(_input(expected_value=5.0))
        assert result.execute is False
        assert any("expected_value" in r for r in result.rejection_reasons)

    def test_reject_low_final_score(self):
        cfg = TruthEngineConfig(min_final_score=100.0)  # Impossible threshold
        gate = DecisionGate(config=cfg)
        result = gate.evaluate(_input())
        assert result.execute is False
        assert any("final_score" in r for r in result.rejection_reasons)

    def test_high_risk_reduces_score(self):
        gate = DecisionGate()
        low_risk = gate.evaluate(_input(risk_score=10.0))
        high_risk = gate.evaluate(_input(risk_score=90.0))
        assert low_risk.final_score > high_risk.final_score

    def test_final_score_formula(self):
        gate = DecisionGate()
        di = _input(
            expected_value=20.0,
            confidence_score=80.0,
            liquidity_score=60.0,
            risk_score=40.0,
        )
        result = gate.evaluate(di)
        # Manual calculation:
        # EV * (80/100) * (60/100) * (1 - 40/100) = 20 * 0.8 * 0.6 * 0.6 = 5.76
        assert result.final_score == pytest.approx(5.76, abs=0.01)

    def test_stores_inputs_in_result(self):
        gate = DecisionGate()
        di = _input()
        result = gate.evaluate(di)
        assert result.inputs is di


class TestExpectedValue:

    def test_computation(self):
        gate = DecisionGate()
        ev = gate.compute_expected_value(50.0, 0.6)
        assert ev == 30.0

    def test_zero_probability(self):
        gate = DecisionGate()
        ev = gate.compute_expected_value(50.0, 0.0)
        assert ev == 0.0


class TestProbabilityOfSale:

    def test_with_history(self):
        gate = DecisionGate()
        p = gate.estimate_probability_of_sale(
            confidence_score=80.0,
            liquidity_score=70.0,
            historical_precision=0.75,
        )
        # hist * 0.50 + conf * 0.30 + liq * 0.20
        # 0.75*0.50 + 0.8*0.30 + 0.7*0.20 = 0.375 + 0.24 + 0.14 = 0.755
        assert p == pytest.approx(0.755, abs=0.01)

    def test_without_history(self):
        gate = DecisionGate()
        p = gate.estimate_probability_of_sale(
            confidence_score=80.0,
            liquidity_score=70.0,
            historical_precision=0.0,
        )
        # No history: conf * 0.60 + liq * 0.40
        # 0.8*0.60 + 0.7*0.40 = 0.48 + 0.28 = 0.76
        assert p == pytest.approx(0.76, abs=0.01)

    def test_clamped_to_bounds(self):
        gate = DecisionGate()
        p_high = gate.estimate_probability_of_sale(100.0, 100.0, 1.0)
        p_low = gate.estimate_probability_of_sale(0.0, 0.0, 0.0)
        assert p_high <= 0.99
        assert p_low >= 0.01
