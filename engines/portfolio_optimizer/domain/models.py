"""
Domain models for Portfolio Optimizer.

Pure value objects — no infrastructure dependencies.

OpportunityCandidate: a scored opportunity eligible for execution
PortfolioState: current capital and position constraints
OptimizationResult: output of the optimizer — approved/rejected split with reasons
"""
from dataclasses import dataclass, field
from enum import Enum


class RejectionReason(str, Enum):
    INSUFFICIENT_CAPITAL = "insufficient_capital"
    MAX_POSITIONS_REACHED = "max_positions_reached"
    RISK_TOO_HIGH = "risk_too_high"
    PRODUCT_CONCENTRATION = "product_concentration"
    MARKETPLACE_CONCENTRATION = "marketplace_concentration"
    DUPLICATE_OPPORTUNITY = "duplicate_opportunity"
    BELOW_CUTOFF = "below_cutoff"


@dataclass(frozen=True, slots=True)
class OpportunityCandidate:
    """An opportunity eligible for portfolio selection.

    Immutable value object — optimizer never mutates candidates.
    """
    opportunity_id: int
    product_id: int
    buy_marketplace: str
    sell_marketplace: str
    buy_price: float
    sell_price: float
    expected_profit: float
    confidence: float          # 0-100
    risk_score: float          # 0-100 (lower = safer)
    capital_required: float    # buy_price * quantity
    velocity_score: float      # 0-100 (market demand signal)
    score: float = 0.0         # orchestrator decision score
    signal_strength: str = "moderate"

    @property
    def roi(self) -> float:
        if self.capital_required <= 0:
            return 0.0
        return self.expected_profit / self.capital_required


@dataclass
class PortfolioState:
    """Mutable snapshot of current portfolio constraints.

    Updated in-place during knapsack selection.
    """
    total_capital: float
    available_capital: float
    allocated_capital: float = 0.0
    max_risk_per_trade: float = 70.0       # max risk_score allowed
    max_open_positions: int = 20
    active_positions: int = 0

    # Concentration tracking (populated during selection)
    positions_by_product: dict[int, int] = field(default_factory=dict)
    positions_by_marketplace: dict[str, int] = field(default_factory=dict)
    selected_opportunity_ids: set[int] = field(default_factory=set)

    @property
    def remaining_slots(self) -> int:
        return max(0, self.max_open_positions - self.active_positions)

    def can_allocate(self, amount: float) -> bool:
        return amount <= self.available_capital

    def allocate(self, candidate: OpportunityCandidate) -> None:
        """Commit capital and update concentration counters."""
        self.available_capital -= candidate.capital_required
        self.allocated_capital += candidate.capital_required
        self.active_positions += 1
        self.selected_opportunity_ids.add(candidate.opportunity_id)
        self.positions_by_product[candidate.product_id] = (
            self.positions_by_product.get(candidate.product_id, 0) + 1
        )
        route = f"{candidate.buy_marketplace}->{candidate.sell_marketplace}"
        self.positions_by_marketplace[route] = (
            self.positions_by_marketplace.get(route, 0) + 1
        )


@dataclass(frozen=True, slots=True)
class RejectedCandidate:
    """A candidate that was not selected, with the reason."""
    candidate: OpportunityCandidate
    reason: RejectionReason


@dataclass
class OptimizationResult:
    """Output of the portfolio optimizer."""
    approved: list[OpportunityCandidate] = field(default_factory=list)
    rejected: list[RejectedCandidate] = field(default_factory=list)
    total_capital_allocated: float = 0.0
    total_expected_profit: float = 0.0

    @property
    def approval_rate(self) -> float:
        total = len(self.approved) + len(self.rejected)
        return len(self.approved) / total if total > 0 else 0.0

    @property
    def rejection_summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.rejected:
            counts[r.reason.value] = counts.get(r.reason.value, 0) + 1
        return counts
