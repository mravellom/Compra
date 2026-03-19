"""
Capital Manager — execution discipline layer for automated arbitrage.

Orchestrates:
  1. Opportunity ranking by composite score
  2. Position sizing via the capital management system
  3. Per-product and per-marketplace cooldowns
  4. Concurrent trade limits
  5. Stop conditions (drawdown, success rate, drift)
  6. Dry-run mode (simulate everything, commit nothing)
  7. Capital utilization and performance metrics

Sits between the validation layer (arbitrage_validator) and the
execution layer (capital_management/execution_guard).  Consumes
runtime config feedback for adaptive thresholds.
"""
import logging
import time
from dataclasses import dataclass, field

from capital_management.config import CapitalConfig, get_config
from capital_management.execution_guard import ExecutionGuard
from capital_management.models import (
    ExecutionGuardResult,
    PortfolioState,
    Position,
    PositionStatus,
)
from capital_management.portfolio_tracker import PortfolioTracker

logger = logging.getLogger(__name__)


# ── Configuration ────────────────────────────────────────────

@dataclass
class ManagerConfig:
    """All orchestration knobs in one place."""

    # Concurrent trade limits
    max_active_trades: int = 10

    # Cooldowns (seconds)
    product_cooldown_seconds: float = 600.0      # 10 min per product
    marketplace_cooldown_seconds: float = 120.0   # 2 min per marketplace

    # Ranking
    top_k: int = 5                               # execute top K per cycle

    # Stop conditions
    stop_daily_loss_pct: float = 0.05            # 5% of total capital
    stop_min_success_rate: float = 0.50          # halt below 50% win rate
    stop_min_trades_for_rate: int = 10           # need N trades before checking rate
    stop_max_drawdown_pct: float = 0.15          # 15% max drawdown

    # Confidence-scaled sizing
    confidence_scaling: bool = True              # scale allocation by confidence
    min_confidence_for_full_size: float = 0.80   # below this = partial sizing


# ── Opportunity candidate ────────────────────────────────────

@dataclass
class OpportunityCandidate:
    """An opportunity scored and ready for execution consideration."""

    opportunity_id: int
    product_id: int
    buy_marketplace: str
    sell_marketplace: str
    category: str
    buy_price: float
    sell_price: float
    expected_profit: float
    expected_roi: float

    # Scores (from scoring + validation layers)
    opportunity_score: float          # 0-100
    risk_score: float                 # 0-100
    execution_confidence: float       # 0-1
    capital_required: float


# ── Execution decision ───────────────────────────────────────

@dataclass(frozen=True)
class ExecutionDecision:
    """Result of the capital manager's evaluation."""

    opportunity_id: int
    approved: bool
    allocation: float = 0.0
    rejection_reasons: list[str] = field(default_factory=list)
    rank: int = 0
    dry_run: bool = False
    guard_result: ExecutionGuardResult | None = None


# ── Performance metrics ──────────────────────────────────────

@dataclass
class CapitalMetrics:
    """Aggregate performance tracking."""

    total_capital: float = 0.0
    available_capital: float = 0.0
    allocated_capital: float = 0.0
    utilization_pct: float = 0.0
    realized_profit: float = 0.0
    drawdown_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    active_positions: int = 0
    avg_profit_per_trade: float = 0.0
    daily_loss_usd: float = 0.0
    is_halted: bool = False
    halt_reasons: list[str] = field(default_factory=list)


# ── Core manager ─────────────────────────────────────────────

class CapitalManager:
    """Orchestrates opportunity selection, sizing, and execution discipline.

    Usage:
        manager = CapitalManager(portfolio)

        # Rank and filter a batch of candidates:
        decisions = manager.evaluate_batch(candidates)

        # After execution completes:
        manager.record_win(opportunity_id, profit)
        manager.record_loss(opportunity_id, loss)

        # Check system health:
        metrics = manager.get_metrics()
    """

    def __init__(
        self,
        portfolio: PortfolioState,
        capital_config: CapitalConfig | None = None,
        manager_config: ManagerConfig | None = None,
    ) -> None:
        self._portfolio = portfolio
        self._cap_cfg = capital_config or get_config()
        self._cfg = manager_config or ManagerConfig()
        self._guard = ExecutionGuard(self._cap_cfg)
        self._tracker = PortfolioTracker()

        # Cooldown state: key → expiry timestamp
        self._product_cooldowns: dict[int, float] = {}
        self._marketplace_cooldowns: dict[str, float] = {}

        # Halt state
        self._halted = False
        self._halt_reasons: list[str] = []

    @property
    def portfolio(self) -> PortfolioState:
        return self._portfolio

    @property
    def is_halted(self) -> bool:
        return self._halted

    # ── Batch evaluation ─────────────────────────────────────

    def evaluate_batch(
        self,
        candidates: list[OpportunityCandidate],
        *,
        dry_run: bool = False,
    ) -> list[ExecutionDecision]:
        """Rank, filter, and size a batch of opportunity candidates.

        Steps:
        1. Check stop conditions (system-wide halt)
        2. Rank candidates by composite score
        3. Take top K
        4. For each: check cooldowns → execution guard → allocate

        Args:
            candidates: Opportunities to consider.
            dry_run: If True, simulate everything but don't commit capital.

        Returns:
            List of ExecutionDecision for each candidate (approved or not).
        """
        # Step 1: check stop conditions
        self._check_stop_conditions()
        if self._halted:
            return [
                ExecutionDecision(
                    opportunity_id=c.opportunity_id,
                    approved=False,
                    rejection_reasons=list(self._halt_reasons),
                    dry_run=dry_run,
                )
                for c in candidates
            ]

        # Step 2: rank
        ranked = self._rank_candidates(candidates)

        # Step 3: take top K
        top = ranked[:self._cfg.top_k]
        rejected_overflow = ranked[self._cfg.top_k:]

        decisions: list[ExecutionDecision] = []
        rank = 0

        # Step 4: evaluate each
        for candidate in top:
            rank += 1
            decision = self._evaluate_single(candidate, rank, dry_run)
            decisions.append(decision)

        # Append rejected-by-rank
        for candidate in rejected_overflow:
            rank += 1
            decisions.append(ExecutionDecision(
                opportunity_id=candidate.opportunity_id,
                approved=False,
                rejection_reasons=[f"ranked {rank} > top_k={self._cfg.top_k}"],
                rank=rank,
                dry_run=dry_run,
            ))

        return decisions

    def _evaluate_single(
        self,
        candidate: OpportunityCandidate,
        rank: int,
        dry_run: bool,
    ) -> ExecutionDecision:
        """Evaluate a single candidate through all gates."""
        reasons: list[str] = []

        # Gate 1: concurrent trade limit
        active = self._portfolio.open_position_count
        if active >= self._cfg.max_active_trades:
            reasons.append(
                f"max_active_trades={self._cfg.max_active_trades} reached ({active} open)"
            )
            return ExecutionDecision(
                opportunity_id=candidate.opportunity_id,
                approved=False, rejection_reasons=reasons,
                rank=rank, dry_run=dry_run,
            )

        # Gate 2: cooldowns
        cooldown_reason = self._check_cooldowns(candidate)
        if cooldown_reason:
            reasons.append(cooldown_reason)
            return ExecutionDecision(
                opportunity_id=candidate.opportunity_id,
                approved=False, rejection_reasons=reasons,
                rank=rank, dry_run=dry_run,
            )

        # Gate 3: confidence-scaled sizing
        capital_required = candidate.capital_required
        if self._cfg.confidence_scaling:
            scale = self._confidence_scale(candidate.execution_confidence)
            capital_required = capital_required * scale

        # Gate 4: execution guard (risk + sizing + diversification)
        guard_result = self._guard.evaluate(
            opportunity_id=candidate.opportunity_id,
            opportunity_score=candidate.opportunity_score,
            risk_score=candidate.risk_score,
            capital_required=capital_required,
            expected_profit=candidate.expected_profit,
            expected_roi=candidate.expected_roi,
            portfolio=self._portfolio,
            product_id=candidate.product_id,
            marketplace=candidate.sell_marketplace,
            category=candidate.category,
        )

        if not guard_result.approved:
            return ExecutionDecision(
                opportunity_id=candidate.opportunity_id,
                approved=False,
                rejection_reasons=guard_result.rejection_reasons,
                rank=rank, dry_run=dry_run,
                guard_result=guard_result,
            )

        allocation = guard_result.final_allocation

        # Gate 5: commit (unless dry-run)
        if not dry_run:
            position = Position(
                opportunity_id=candidate.opportunity_id,
                allocated_amount=allocation,
                entry_price=candidate.buy_price,
                expected_profit=candidate.expected_profit,
                expected_roi=candidate.expected_roi,
                risk_score=candidate.risk_score,
                product_id=candidate.product_id,
                marketplace=candidate.sell_marketplace,
                category=candidate.category,
            )
            self._tracker.open_position(self._portfolio, position)
            self._set_cooldowns(candidate)

        logger.info(
            "%s opp=%d rank=%d alloc=$%.2f profit=$%.2f%s",
            "DRY-RUN" if dry_run else "EXECUTE",
            candidate.opportunity_id, rank, allocation,
            candidate.expected_profit,
            " (simulated)" if dry_run else "",
        )

        return ExecutionDecision(
            opportunity_id=candidate.opportunity_id,
            approved=True,
            allocation=allocation,
            rank=rank,
            dry_run=dry_run,
            guard_result=guard_result,
        )

    # ── Ranking ──────────────────────────────────────────────

    def _rank_candidates(
        self, candidates: list[OpportunityCandidate],
    ) -> list[OpportunityCandidate]:
        """Rank by composite score: confidence × profit / risk."""
        def score(c: OpportunityCandidate) -> float:
            risk_factor = max(0.01, 1.0 - c.risk_score / 100.0)
            return c.execution_confidence * c.expected_profit * risk_factor

        return sorted(candidates, key=score, reverse=True)

    # ── Cooldowns ────────────────────────────────────────────

    def _check_cooldowns(self, c: OpportunityCandidate) -> str:
        """Return rejection reason if any cooldown is active, else empty string."""
        now = time.time()

        # Product cooldown
        if c.product_id in self._product_cooldowns:
            expires = self._product_cooldowns[c.product_id]
            if now < expires:
                remaining = expires - now
                return f"product {c.product_id} in cooldown ({remaining:.0f}s remaining)"

        # Marketplace cooldown
        for mp in (c.buy_marketplace, c.sell_marketplace):
            if mp in self._marketplace_cooldowns:
                expires = self._marketplace_cooldowns[mp]
                if now < expires:
                    remaining = expires - now
                    return f"marketplace {mp} in cooldown ({remaining:.0f}s remaining)"

        return ""

    def _set_cooldowns(self, c: OpportunityCandidate) -> None:
        """Activate cooldowns after a trade is committed."""
        now = time.time()
        self._product_cooldowns[c.product_id] = now + self._cfg.product_cooldown_seconds
        self._marketplace_cooldowns[c.buy_marketplace] = now + self._cfg.marketplace_cooldown_seconds
        self._marketplace_cooldowns[c.sell_marketplace] = now + self._cfg.marketplace_cooldown_seconds

    # ── Confidence scaling ───────────────────────────────────

    def _confidence_scale(self, confidence: float) -> float:
        """Scale position size by confidence. 1.0 at full confidence, down to 0.5."""
        if confidence >= self._cfg.min_confidence_for_full_size:
            return 1.0
        # Linear scale: 0.0 conf → 0.5, min_conf → 1.0
        floor = 0.5
        ratio = confidence / self._cfg.min_confidence_for_full_size
        return floor + ratio * (1.0 - floor)

    # ── Stop conditions ──────────────────────────────────────

    def _check_stop_conditions(self) -> None:
        """Check system-wide halt conditions."""
        p = self._portfolio
        reasons: list[str] = []

        # Drawdown halt
        if p.drawdown_pct >= self._cfg.stop_max_drawdown_pct:
            reasons.append(
                f"drawdown {p.drawdown_pct:.1%} >= {self._cfg.stop_max_drawdown_pct:.0%}"
            )

        # Daily loss halt
        daily_limit = p.total_capital * self._cfg.stop_daily_loss_pct
        if daily_limit > 0 and p.daily_loss_usd >= daily_limit:
            reasons.append(
                f"daily_loss ${p.daily_loss_usd:.2f} >= ${daily_limit:.2f} "
                f"({self._cfg.stop_daily_loss_pct:.0%} of capital)"
            )

        # Success rate halt (only after enough trades)
        if (
            p.total_trades >= self._cfg.stop_min_trades_for_rate
            and p.win_rate < self._cfg.stop_min_success_rate
        ):
            reasons.append(
                f"win_rate {p.win_rate:.1%} < {self._cfg.stop_min_success_rate:.0%} "
                f"(after {p.total_trades} trades)"
            )

        if reasons:
            self._halted = True
            self._halt_reasons = reasons
            logger.warning("Capital manager HALTED: %s", "; ".join(reasons))
        else:
            self._halted = False
            self._halt_reasons = []

    def resume(self) -> None:
        """Manually resume after a halt (use with caution)."""
        self._halted = False
        self._halt_reasons = []
        logger.info("Capital manager resumed manually")

    # ── Trade lifecycle ──────────────────────────────────────

    def record_win(self, opportunity_id: int, actual_profit: float) -> None:
        """Record a successful trade."""
        self._tracker.close_position_success(
            self._portfolio, opportunity_id, actual_profit,
        )

    def record_loss(self, opportunity_id: int, loss_amount: float) -> None:
        """Record a failed trade."""
        self._tracker.close_position_failure(
            self._portfolio, opportunity_id, loss_amount,
        )

    # ── Metrics ──────────────────────────────────────────────

    def get_metrics(self) -> CapitalMetrics:
        """Snapshot of capital utilization and performance."""
        p = self._portfolio
        return CapitalMetrics(
            total_capital=round(p.total_capital, 2),
            available_capital=round(p.available_capital, 2),
            allocated_capital=round(p.allocated_capital, 2),
            utilization_pct=round(p.allocation_pct * 100, 1),
            realized_profit=round(p.realized_profit, 2),
            drawdown_pct=round(p.drawdown_pct * 100, 2),
            max_drawdown_pct=round(p.max_drawdown_pct * 100, 2),
            win_rate=round(p.win_rate * 100, 1),
            total_trades=p.total_trades,
            winning_trades=p.winning_trades,
            losing_trades=p.losing_trades,
            active_positions=p.open_position_count,
            avg_profit_per_trade=round(p.avg_profit_per_trade, 2),
            daily_loss_usd=round(p.daily_loss_usd, 2),
            is_halted=self._halted,
            halt_reasons=list(self._halt_reasons),
        )

    def clear_cooldowns(self) -> None:
        """Reset all cooldowns (for testing)."""
        self._product_cooldowns.clear()
        self._marketplace_cooldowns.clear()
