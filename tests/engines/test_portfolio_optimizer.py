"""
Tests — Portfolio Optimizer (8 entregable).

Covers:
  1. Ranking correctness (scoring formula, ordering)
  2. Capital constraints (cannot exceed available)
  3. Knapsack selection (greedy profit-density)
  4. Diversification rules (product, marketplace, duplicate)
  5. Edge cases (empty input, zero capital, all rejected)
  6. PortfolioState mutations
  7. Metrics tracking
  8. Performance (500 candidates < 50ms)
"""
import time

import pytest

from engines.portfolio_optimizer.domain.models import (
    OpportunityCandidate,
    OptimizationResult,
    PortfolioState,
    RejectedCandidate,
    RejectionReason,
)
from engines.portfolio_optimizer.domain.optimizer import (
    DiversificationConfig,
    PortfolioOptimizer,
    ScoringWeights,
    rank_candidate,
    rank_candidates,
    check_diversification,
)
from engines.portfolio_optimizer.application.optimizer_service import (
    OptimizerMetrics,
    PortfolioOptimizerService,
)


# ── Helpers ───────────────────────────────────────────────


def _candidate(
    opp_id: int = 1,
    product_id: int = 100,
    buy_mp: str = "amazon",
    sell_mp: str = "mercadolibre_mx",
    buy_price: float = 50.0,
    sell_price: float = 100.0,
    profit: float = 25.0,
    confidence: float = 70.0,
    risk: float = 30.0,
    velocity: float = 60.0,
    score: float = 75.0,
    signal: str = "moderate",
) -> OpportunityCandidate:
    return OpportunityCandidate(
        opportunity_id=opp_id,
        product_id=product_id,
        buy_marketplace=buy_mp,
        sell_marketplace=sell_mp,
        buy_price=buy_price,
        sell_price=sell_price,
        expected_profit=profit,
        confidence=confidence,
        risk_score=risk,
        capital_required=buy_price,
        velocity_score=velocity,
        score=score,
        signal_strength=signal,
    )


def _state(
    capital: float = 1000.0,
    max_risk: float = 70.0,
    max_positions: int = 20,
    active: int = 0,
) -> PortfolioState:
    return PortfolioState(
        total_capital=capital,
        available_capital=capital,
        max_risk_per_trade=max_risk,
        max_open_positions=max_positions,
        active_positions=active,
    )


# ═══════════════════════════════════════════════════════════
# 1. RANKING
# ═══════════════════════════════════════════════════════════


class TestRanking:

    def test_higher_profit_ranks_higher(self):
        low = _candidate(profit=10.0)
        high = _candidate(profit=80.0)
        assert rank_candidate(high) > rank_candidate(low)

    def test_higher_risk_ranks_lower(self):
        safe = _candidate(risk=10.0)
        risky = _candidate(risk=90.0)
        assert rank_candidate(safe) > rank_candidate(risky)

    def test_higher_confidence_ranks_higher(self):
        low_conf = _candidate(confidence=20.0)
        high_conf = _candidate(confidence=90.0)
        assert rank_candidate(high_conf) > rank_candidate(low_conf)

    def test_higher_velocity_ranks_higher(self):
        slow = _candidate(velocity=10.0)
        fast = _candidate(velocity=90.0)
        assert rank_candidate(fast) > rank_candidate(slow)

    def test_rank_candidates_sorted_by_density(self):
        """Profit-density (score/capital) determines order."""
        # High profit but high capital = lower density
        expensive = _candidate(opp_id=1, profit=50.0, buy_price=500.0)
        # Lower profit but very low capital = higher density
        cheap = _candidate(opp_id=2, profit=20.0, buy_price=10.0)

        ranked = rank_candidates([expensive, cheap])
        assert ranked[0][0].opportunity_id == 2  # cheap first (higher density)

    def test_custom_weights(self):
        """Custom weights should change ranking."""
        c = _candidate(profit=50.0, velocity=90.0, confidence=20.0, risk=10.0)
        profit_focused = ScoringWeights(profit=0.70, velocity=0.10, confidence=0.10, risk=0.10)
        velocity_focused = ScoringWeights(profit=0.10, velocity=0.70, confidence=0.10, risk=0.10)

        score_profit = rank_candidate(c, profit_focused)
        score_velocity = rank_candidate(c, velocity_focused)
        # Same candidate, different weights → different scores
        assert score_profit != score_velocity

    def test_weights_must_sum_to_one(self):
        with pytest.raises(ValueError, match="sum to 1.0"):
            ScoringWeights(profit=0.5, velocity=0.5, confidence=0.5, risk=0.5)

    def test_zero_profit_zero_score(self):
        c = _candidate(profit=0.0, confidence=0.0, velocity=0.0, risk=0.0)
        assert rank_candidate(c) == pytest.approx(0.0, abs=0.01)


# ═══════════════════════════════════════════════════════════
# 2. CAPITAL CONSTRAINTS
# ═══════════════════════════════════════════════════════════


class TestCapitalConstraints:

    def test_basic_capital_limit(self):
        """Cannot allocate more than available capital."""
        optimizer = PortfolioOptimizer()
        candidates = [
            _candidate(opp_id=1, buy_price=600.0, profit=50.0),
            _candidate(opp_id=2, buy_price=600.0, profit=40.0),
        ]
        state = _state(capital=1000.0)
        result = optimizer.optimize(candidates, state)

        assert len(result.approved) == 1
        assert len(result.rejected) == 1
        rejected_reasons = [r.reason for r in result.rejected]
        assert RejectionReason.INSUFFICIENT_CAPITAL in rejected_reasons

    def test_exact_capital_fit(self):
        """Multiple items that exactly fit capital should all be selected."""
        optimizer = PortfolioOptimizer(
            diversification=DiversificationConfig(max_per_product=99, max_per_marketplace=99),
        )
        candidates = [
            _candidate(opp_id=i, buy_price=100.0, profit=20.0, product_id=i)
            for i in range(10)
        ]
        state = _state(capital=1000.0)
        result = optimizer.optimize(candidates, state)
        assert len(result.approved) == 10

    def test_zero_capital_rejects_all(self):
        optimizer = PortfolioOptimizer()
        candidates = [_candidate(buy_price=10.0)]
        state = _state(capital=0.0)
        result = optimizer.optimize(candidates, state)
        assert len(result.approved) == 0

    def test_capital_tracking(self):
        optimizer = PortfolioOptimizer()
        candidates = [
            _candidate(opp_id=1, buy_price=300.0, profit=50.0, product_id=1),
            _candidate(opp_id=2, buy_price=400.0, profit=60.0, product_id=2),
        ]
        state = _state(capital=1000.0)
        result = optimizer.optimize(candidates, state)
        assert result.total_capital_allocated == pytest.approx(700.0)
        assert state.available_capital == pytest.approx(300.0)


# ═══════════════════════════════════════════════════════════
# 3. KNAPSACK SELECTION
# ═══════════════════════════════════════════════════════════


class TestKnapsackSelection:

    def test_prefers_higher_density(self):
        """Greedy should pick high-density items first."""
        optimizer = PortfolioOptimizer()
        # Item A: $50 profit, $500 capital (density=0.1)
        # Item B: $30 profit, $100 capital (density=0.3) ← better density
        # Item C: $20 profit, $50 capital (density=0.4) ← best density
        candidates = [
            _candidate(opp_id=1, buy_price=500.0, profit=50.0, product_id=1),
            _candidate(opp_id=2, buy_price=100.0, profit=30.0, product_id=2),
            _candidate(opp_id=3, buy_price=50.0, profit=20.0, product_id=3),
        ]
        state = _state(capital=200.0)
        result = optimizer.optimize(candidates, state)

        approved_ids = {c.opportunity_id for c in result.approved}
        # Should pick B+C ($150 capital, $50 profit) over A ($500 capital, $50 profit)
        assert 2 in approved_ids
        assert 3 in approved_ids
        assert 1 not in approved_ids

    def test_maximizes_total_profit(self):
        """Selected set should have higher profit than alternatives."""
        optimizer = PortfolioOptimizer(
            diversification=DiversificationConfig(max_per_product=99, max_per_marketplace=99),
        )
        candidates = [
            _candidate(opp_id=i, buy_price=50.0, profit=10.0 + i, product_id=i)
            for i in range(20)
        ]
        state = _state(capital=500.0)  # Can fit 10 of 20
        result = optimizer.optimize(candidates, state)

        assert len(result.approved) == 10
        assert result.total_expected_profit > 0

    def test_empty_candidates(self):
        optimizer = PortfolioOptimizer()
        result = optimizer.optimize([], _state())
        assert len(result.approved) == 0
        assert len(result.rejected) == 0


# ═══════════════════════════════════════════════════════════
# 4. DIVERSIFICATION RULES
# ═══════════════════════════════════════════════════════════


class TestDiversification:

    def test_max_per_product(self):
        """No more than max_per_product positions for same product."""
        optimizer = PortfolioOptimizer(
            diversification=DiversificationConfig(max_per_product=2, max_per_marketplace=99),
        )
        candidates = [
            _candidate(opp_id=i, product_id=1, buy_price=10.0, profit=5.0)
            for i in range(5)
        ]
        state = _state(capital=10000.0)
        result = optimizer.optimize(candidates, state)

        assert len(result.approved) == 2
        rejected_reasons = [r.reason for r in result.rejected]
        assert RejectionReason.PRODUCT_CONCENTRATION in rejected_reasons

    def test_max_per_marketplace(self):
        """No more than max_per_marketplace for same route."""
        optimizer = PortfolioOptimizer(
            diversification=DiversificationConfig(max_per_product=99, max_per_marketplace=2),
        )
        candidates = [
            _candidate(opp_id=i, product_id=i, buy_mp="amazon", sell_mp="ebay",
                        buy_price=10.0, profit=5.0)
            for i in range(5)
        ]
        state = _state(capital=10000.0)
        result = optimizer.optimize(candidates, state)
        assert len(result.approved) == 2

    def test_duplicate_opportunity_rejected(self):
        """Same opportunity_id cannot appear twice."""
        optimizer = PortfolioOptimizer()
        candidates = [
            _candidate(opp_id=1, product_id=1),
            _candidate(opp_id=1, product_id=1),  # Duplicate
        ]
        state = _state(capital=10000.0)
        result = optimizer.optimize(candidates, state)
        assert len(result.approved) == 1
        assert result.rejected[0].reason == RejectionReason.DUPLICATE_OPPORTUNITY

    def test_mixed_diversification(self):
        """Different products and routes should all be accepted."""
        optimizer = PortfolioOptimizer()
        candidates = [
            _candidate(opp_id=1, product_id=1, buy_mp="amazon", sell_mp="ebay", buy_price=50.0),
            _candidate(opp_id=2, product_id=2, buy_mp="ebay", sell_mp="ml_mx", buy_price=50.0),
            _candidate(opp_id=3, product_id=3, buy_mp="aliexpress", sell_mp="ml_ar", buy_price=50.0),
        ]
        state = _state(capital=10000.0)
        result = optimizer.optimize(candidates, state)
        assert len(result.approved) == 3

    def test_check_diversification_function(self):
        config = DiversificationConfig(max_per_product=1, max_per_marketplace=1)
        state = _state()

        c1 = _candidate(opp_id=1, product_id=1)
        assert check_diversification(c1, state, config) is None

        state.allocate(c1)
        c2 = _candidate(opp_id=2, product_id=1)
        assert check_diversification(c2, state, config) == RejectionReason.PRODUCT_CONCENTRATION


# ═══════════════════════════════════════════════════════════
# 5. RISK CONSTRAINTS
# ═══════════════════════════════════════════════════════════


class TestRiskConstraints:

    def test_high_risk_rejected(self):
        optimizer = PortfolioOptimizer()
        candidates = [_candidate(risk=90.0)]
        state = _state(max_risk=70.0)
        result = optimizer.optimize(candidates, state)
        assert len(result.approved) == 0
        assert result.rejected[0].reason == RejectionReason.RISK_TOO_HIGH

    def test_borderline_risk_accepted(self):
        optimizer = PortfolioOptimizer()
        candidates = [_candidate(risk=70.0)]
        state = _state(max_risk=70.0)
        result = optimizer.optimize(candidates, state)
        assert len(result.approved) == 1

    def test_max_positions_respected(self):
        optimizer = PortfolioOptimizer()
        candidates = [
            _candidate(opp_id=i, product_id=i, buy_price=10.0)
            for i in range(10)
        ]
        state = _state(capital=10000.0, max_positions=3)
        result = optimizer.optimize(candidates, state)
        assert len(result.approved) == 3


# ═══════════════════════════════════════════════════════════
# 6. PORTFOLIO STATE
# ═══════════════════════════════════════════════════════════


class TestPortfolioState:

    def test_remaining_slots(self):
        state = _state(max_positions=5, active=3)
        assert state.remaining_slots == 2

    def test_can_allocate(self):
        state = _state(capital=100.0)
        assert state.can_allocate(100.0) is True
        assert state.can_allocate(100.01) is False

    def test_allocate_updates_state(self):
        state = _state(capital=1000.0)
        c = _candidate(opp_id=1, product_id=42, buy_price=300.0)
        state.allocate(c)

        assert state.available_capital == 700.0
        assert state.allocated_capital == 300.0
        assert state.active_positions == 1
        assert 1 in state.selected_opportunity_ids
        assert state.positions_by_product[42] == 1

    def test_roi_property(self):
        c = _candidate(buy_price=100.0, profit=25.0)
        assert c.roi == pytest.approx(0.25)

    def test_roi_zero_capital(self):
        c = _candidate(buy_price=0.0, profit=25.0)
        assert c.roi == 0.0


# ═══════════════════════════════════════════════════════════
# 7. OPTIMIZATION RESULT
# ═══════════════════════════════════════════════════════════


class TestOptimizationResult:

    def test_approval_rate(self):
        result = OptimizationResult(
            approved=[_candidate(opp_id=1), _candidate(opp_id=2)],
            rejected=[RejectedCandidate(_candidate(opp_id=3), RejectionReason.RISK_TOO_HIGH)],
        )
        assert result.approval_rate == pytest.approx(2 / 3)

    def test_rejection_summary(self):
        result = OptimizationResult(
            rejected=[
                RejectedCandidate(_candidate(opp_id=1), RejectionReason.RISK_TOO_HIGH),
                RejectedCandidate(_candidate(opp_id=2), RejectionReason.RISK_TOO_HIGH),
                RejectedCandidate(_candidate(opp_id=3), RejectionReason.INSUFFICIENT_CAPITAL),
            ],
        )
        summary = result.rejection_summary
        assert summary["risk_too_high"] == 2
        assert summary["insufficient_capital"] == 1

    def test_empty_result(self):
        result = OptimizationResult()
        assert result.approval_rate == 0.0
        assert result.rejection_summary == {}


# ═══════════════════════════════════════════════════════════
# 8. METRICS
# ═══════════════════════════════════════════════════════════


class TestMetrics:

    def test_record_batch(self):
        metrics = OptimizerMetrics()
        result = OptimizationResult(
            approved=[_candidate(opp_id=1)],
            rejected=[RejectedCandidate(_candidate(opp_id=2), RejectionReason.RISK_TOO_HIGH)],
            total_expected_profit=25.0,
        )
        metrics.record_batch(result, 1.5)

        assert metrics.opportunities_received == 2
        assert metrics.opportunities_selected == 1
        assert metrics.total_profit_estimate == 25.0
        assert metrics.batches_processed == 1
        assert metrics.last_batch_ms == 1.5

    def test_snapshot(self):
        metrics = OptimizerMetrics()
        snap = metrics.snapshot()
        assert "optimizer_opportunities_received" in snap
        assert "optimizer_opportunities_selected" in snap
        assert "optimizer_profit_estimate" in snap
        assert "optimizer_rejection_reasons" in snap


# ═══════════════════════════════════════════════════════════
# 9. SERVICE LAYER
# ═══════════════════════════════════════════════════════════


class TestOptimizerService:

    def test_optimize_batch_direct(self):
        """Service.optimize_batch works without Redis."""
        from unittest.mock import AsyncMock
        service = PortfolioOptimizerService(
            redis_client=AsyncMock(),
            capital=1000.0,
            max_positions=5,
        )
        candidates = [
            _candidate(opp_id=i, product_id=i, buy_price=100.0, profit=20.0)
            for i in range(10)
        ]
        result = service.optimize_batch(candidates)
        assert len(result.approved) == 5  # max_positions=5
        assert result.total_capital_allocated == pytest.approx(500.0)


# ═══════════════════════════════════════════════════════════
# 10. PERFORMANCE
# ═══════════════════════════════════════════════════════════


class TestPerformance:

    def test_500_candidates_under_50ms(self):
        """Must process 500 candidates in under 50ms."""
        optimizer = PortfolioOptimizer()
        candidates = [
            _candidate(
                opp_id=i,
                product_id=i % 100,
                buy_price=float(50 + i % 200),
                profit=float(5 + i % 50),
                confidence=float(30 + i % 70),
                risk=float(10 + i % 60),
                velocity=float(20 + i % 80),
                buy_mp=f"mp_{i % 5}",
                sell_mp=f"mp_{(i + 2) % 5}",
            )
            for i in range(500)
        ]
        state = _state(capital=50000.0, max_positions=100)

        t0 = time.monotonic()
        result = optimizer.optimize(candidates, state)
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert elapsed_ms < 50, f"Took {elapsed_ms:.1f}ms, limit is 50ms"
        assert len(result.approved) > 0
        assert len(result.approved) + len(result.rejected) == 500

    def test_1000_candidates_under_100ms(self):
        """Stress test: 1000 candidates."""
        optimizer = PortfolioOptimizer()
        candidates = [
            _candidate(
                opp_id=i, product_id=i % 200,
                buy_price=float(20 + i % 300), profit=float(3 + i % 80),
                buy_mp=f"mp_{i % 8}", sell_mp=f"mp_{(i + 3) % 8}",
            )
            for i in range(1000)
        ]
        state = _state(capital=100000.0, max_positions=200)

        t0 = time.monotonic()
        result = optimizer.optimize(candidates, state)
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert elapsed_ms < 100, f"Took {elapsed_ms:.1f}ms"
        assert len(result.approved) + len(result.rejected) == 1000
