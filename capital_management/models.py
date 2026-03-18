"""
Domain models for the Capital Management System.

Mutable state models (PortfolioState) and immutable value objects (Position, SizingResult).
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class PositionStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"
    FAILED = "failed"


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Position:
    """An active capital allocation tied to an opportunity."""

    opportunity_id: int
    allocated_amount: float
    entry_price: float
    expected_profit: float
    expected_roi: float
    risk_score: float               # 0-100
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: PositionStatus = PositionStatus.OPEN

    # Classification for diversification
    product_id: Optional[int] = None
    marketplace: str = ""
    category: str = ""

    # Outcome (filled on close)
    exit_price: Optional[float] = None
    actual_profit: Optional[float] = None


@dataclass
class PortfolioState:
    """Live portfolio state — the single source of truth for capital."""

    total_capital: float              # Starting + all realized P&L
    available_capital: float          # Capital not currently allocated
    allocated_capital: float = 0.0    # Capital in open positions
    reserved_capital: float = 0.0     # Set aside (e.g. for pending approvals)
    realized_profit: float = 0.0      # Cumulative realized P&L
    unrealized_profit: float = 0.0    # Estimated P&L on open positions
    peak_capital: float = 0.0         # High-water mark for drawdown calc

    # Risk tracking
    drawdown_pct: float = 0.0        # (peak - current) / peak
    max_drawdown_pct: float = 0.0    # Worst drawdown ever seen
    consecutive_losses: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    daily_loss_usd: float = 0.0
    daily_loss_reset_date: Optional[str] = None  # ISO date

    # Active positions
    active_positions: list[Position] = field(default_factory=list)

    # Cooldown
    last_loss_at: Optional[datetime] = None

    def __post_init__(self):
        if self.peak_capital == 0.0:
            self.peak_capital = self.total_capital

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades

    @property
    def avg_profit_per_trade(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.realized_profit / self.total_trades

    @property
    def allocation_pct(self) -> float:
        if self.total_capital <= 0:
            return 0.0
        return self.allocated_capital / self.total_capital

    @property
    def open_position_count(self) -> int:
        return sum(1 for p in self.active_positions if p.status == PositionStatus.OPEN)


@dataclass(frozen=True)
class SizingResult:
    """Output of the position sizer."""

    allocation: float
    is_valid: bool
    reason: str = ""
    risk_adjusted_allocation: float = 0.0
    base_allocation: float = 0.0


@dataclass(frozen=True)
class DiversificationResult:
    """Output of diversification check."""

    is_safe: bool
    violations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RiskCheckResult:
    """Output of the risk manager."""

    can_execute: bool
    risk_level: RiskLevel
    adjustment_factor: float = 1.0   # Multiplier on position size
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExecutionGuardResult:
    """Final gate decision — aggregates all checks."""

    approved: bool
    final_allocation: float = 0.0
    rejection_reasons: list[str] = field(default_factory=list)

    # Sub-results for observability
    risk_check: Optional[RiskCheckResult] = None
    sizing_result: Optional[SizingResult] = None
    diversification_result: Optional[DiversificationResult] = None
