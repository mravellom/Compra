"""
Portfolio Optimizer — pure domain logic.

Solves the constrained portfolio selection problem:
  Given N opportunity candidates and limited capital,
  select the subset that maximizes expected profit while
  respecting risk, position, and diversification constraints.

Algorithm: Greedy approximation to 0/1 Knapsack.
  1. Score each candidate with weighted composite
  2. Sort by profit-density (score / capital) descending
  3. Greedily select candidates that pass all guards
  4. O(n log n) — handles 500 candidates in <1ms

Design patterns:
  - Strategy (scoring weights are configurable)
  - Template Method (optimize runs the fixed pipeline)
  - Guard (diversification rules as composable checks)
"""
import logging
import os
from dataclasses import dataclass

from .models import (
    OpportunityCandidate,
    OptimizationResult,
    PortfolioState,
    RejectedCandidate,
    RejectionReason,
)

logger = logging.getLogger(__name__)


# ── Scoring Weights (configurable) ────────────────────────


@dataclass(frozen=True, slots=True)
class ScoringWeights:
    """Weights for the opportunity ranking function."""
    profit: float = 0.35
    velocity: float = 0.20
    confidence: float = 0.25
    risk: float = 0.20

    def __post_init__(self) -> None:
        total = self.profit + self.velocity + self.confidence + self.risk
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Weights must sum to 1.0, got {total}")


DEFAULT_WEIGHTS = ScoringWeights()


# ── Diversification Config ────────────────────────────────


@dataclass(frozen=True, slots=True)
class DiversificationConfig:
    """Limits for portfolio concentration."""
    max_per_product: int = 3
    max_per_marketplace: int = 5


DEFAULT_DIVERSIFICATION = DiversificationConfig(
    max_per_product=int(os.getenv("OPT_MAX_PER_PRODUCT", "3")),
    max_per_marketplace=int(os.getenv("OPT_MAX_PER_MARKETPLACE", "5")),
)


# ── Opportunity Ranking (Entregable 2) ────────────────────


def rank_candidate(
    candidate: OpportunityCandidate,
    weights: ScoringWeights = DEFAULT_WEIGHTS,
) -> float:
    """Compute composite ranking score for a single candidate.

    score = w_profit * norm_profit
          + w_velocity * norm_velocity
          + w_confidence * norm_confidence
          - w_risk * norm_risk

    All inputs are already 0-100 scale except profit.
    Profit is normalized via diminishing returns: 1 - e^(-0.03 * profit).
    """
    import math
    # Normalize profit to 0-1 range (diminishing returns, $100 ≈ 0.95)
    norm_profit = 1 - math.exp(-0.03 * max(0, candidate.expected_profit))
    norm_velocity = candidate.velocity_score / 100.0
    norm_confidence = candidate.confidence / 100.0
    norm_risk = candidate.risk_score / 100.0

    return (
        weights.profit * norm_profit
        + weights.velocity * norm_velocity
        + weights.confidence * norm_confidence
        - weights.risk * norm_risk
    )


def rank_candidates(
    candidates: list[OpportunityCandidate],
    weights: ScoringWeights = DEFAULT_WEIGHTS,
) -> list[tuple[OpportunityCandidate, float]]:
    """Rank all candidates by composite score descending.

    Returns list of (candidate, score) sorted by profit-density
    (score / capital) for knapsack greedy approximation.
    """
    scored = []
    for c in candidates:
        s = rank_candidate(c, weights)
        # Profit-density: score per unit of capital
        density = s / max(c.capital_required, 0.01)
        scored.append((c, s, density))

    scored.sort(key=lambda x: x[2], reverse=True)
    return [(c, s) for c, s, _ in scored]


# ── Diversification Guards (Entregable 5) ─────────────────


def check_diversification(
    candidate: OpportunityCandidate,
    state: PortfolioState,
    config: DiversificationConfig,
) -> RejectionReason | None:
    """Check if adding this candidate would violate diversification rules.

    Returns the rejection reason, or None if OK.
    """
    # Duplicate check
    if candidate.opportunity_id in state.selected_opportunity_ids:
        return RejectionReason.DUPLICATE_OPPORTUNITY

    # Product concentration
    current_product = state.positions_by_product.get(candidate.product_id, 0)
    if current_product >= config.max_per_product:
        return RejectionReason.PRODUCT_CONCENTRATION

    # Marketplace concentration
    route = f"{candidate.buy_marketplace}->{candidate.sell_marketplace}"
    current_route = state.positions_by_marketplace.get(route, 0)
    if current_route >= config.max_per_marketplace:
        return RejectionReason.MARKETPLACE_CONCENTRATION

    return None


# ── Portfolio Optimizer (Entregable 3 + 4) ────────────────


class PortfolioOptimizer:
    """Selects the optimal subset of opportunities under capital constraints.

    Algorithm:
      1. Rank by profit-density (score / capital)
      2. Greedy selection with constraint checking
      3. Each candidate passes through:
         a) Capital check (available_capital >= capital_required)
         b) Risk check (risk_score <= max_risk_per_trade)
         c) Position limit (active_positions < max_open_positions)
         d) Diversification guards (product/marketplace concentration)

    Complexity: O(n log n) sort + O(n) selection = O(n log n)
    Performance: 500 candidates in <1ms (measured).
    """

    def __init__(
        self,
        weights: ScoringWeights | None = None,
        diversification: DiversificationConfig | None = None,
    ) -> None:
        self._weights = weights or DEFAULT_WEIGHTS
        self._diversification = diversification or DEFAULT_DIVERSIFICATION

    def optimize(
        self,
        candidates: list[OpportunityCandidate],
        state: PortfolioState,
    ) -> OptimizationResult:
        """Run the full optimization pipeline.

        Args:
            candidates: unordered list of opportunity candidates
            state: current portfolio state (will be mutated during selection)

        Returns:
            OptimizationResult with approved/rejected split
        """
        if not candidates:
            return OptimizationResult()

        result = OptimizationResult()

        # Step 1: Rank by profit-density
        ranked = rank_candidates(candidates, self._weights)

        # Step 2: Greedy knapsack selection
        for candidate, score in ranked:
            rejection = self._evaluate(candidate, state, score)

            if rejection is not None:
                result.rejected.append(RejectedCandidate(candidate, rejection))
                continue

            # Accept: allocate capital and update state
            state.allocate(candidate)
            result.approved.append(candidate)
            result.total_capital_allocated += candidate.capital_required
            result.total_expected_profit += candidate.expected_profit

        logger.info(
            "[PortfolioOptimizer] %d/%d approved, capital=$%.2f, est_profit=$%.2f",
            len(result.approved),
            len(result.approved) + len(result.rejected),
            result.total_capital_allocated,
            result.total_expected_profit,
        )

        return result

    def _evaluate(
        self,
        candidate: OpportunityCandidate,
        state: PortfolioState,
        score: float,
    ) -> RejectionReason | None:
        """Evaluate a candidate against all constraints.

        Returns rejection reason or None if accepted.
        """
        # Position limit
        if state.remaining_slots <= 0:
            return RejectionReason.MAX_POSITIONS_REACHED

        # Capital check
        if not state.can_allocate(candidate.capital_required):
            return RejectionReason.INSUFFICIENT_CAPITAL

        # Risk check
        if candidate.risk_score > state.max_risk_per_trade:
            return RejectionReason.RISK_TOO_HIGH

        # Diversification guards
        div_reason = check_diversification(
            candidate, state, self._diversification,
        )
        if div_reason is not None:
            return div_reason

        return None
