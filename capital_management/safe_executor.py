"""
Safe Executor — atomic capital allocation with saga rollback.

Wraps the entire approve→allocate→execute flow in a single DB transaction
with advisory lock. If any step fails, the entire operation rolls back.

Usage:
    executor = SafeExecutor(db_store, execution_guard, portfolio_tracker)
    result = await executor.execute_with_rollback(
        opportunity_id=123,
        opportunity_score=75.0,
        ...
    )
    if result.approved:
        # Capital was atomically allocated and persisted
    else:
        # Nothing happened — fully rolled back
"""
import logging
from dataclasses import dataclass

from .db_store import PortfolioDBStore
from .execution_guard import ExecutionGuard
from .models import ExecutionGuardResult, Position, PositionStatus
from .portfolio_tracker import PortfolioTracker

logger = logging.getLogger(__name__)


@dataclass
class SafeExecutionResult:
    """Result of a safe execution attempt."""
    approved: bool
    guard_result: ExecutionGuardResult | None = None
    position: Position | None = None
    error: str | None = None


class SafeExecutor:
    """Atomic capital allocation with advisory lock and DB persistence.

    The entire flow runs inside a single DB transaction:
    1. Acquire advisory lock (blocks concurrent allocations)
    2. Load portfolio from DB (latest state)
    3. Run ExecutionGuard (all 4 gates)
    4. Open position via PortfolioTracker
    5. Persist updated state to DB
    6. Commit (releases lock)

    If step 4 or 5 fails → transaction rolls back → no capital allocated.
    """

    def __init__(
        self,
        db_store: PortfolioDBStore,
        guard: ExecutionGuard,
        tracker: PortfolioTracker | None = None,
    ):
        self._store = db_store
        self._guard = guard
        self._tracker = tracker or PortfolioTracker()

    async def execute_with_rollback(
        self,
        opportunity_id: int,
        opportunity_score: float,
        risk_score: float,
        capital_required: float,
        expected_profit: float,
        expected_roi: float,
        product_id: int | None = None,
        marketplace: str = "",
        category: str = "",
    ) -> SafeExecutionResult:
        """Atomically evaluate + allocate capital with full rollback on failure."""
        try:
            async with self._store.locked_portfolio() as portfolio:
                # Run all gates against latest DB state
                guard_result = self._guard.evaluate(
                    opportunity_id=opportunity_id,
                    opportunity_score=opportunity_score,
                    risk_score=risk_score,
                    capital_required=capital_required,
                    expected_profit=expected_profit,
                    expected_roi=expected_roi,
                    portfolio=portfolio,
                    product_id=product_id,
                    marketplace=marketplace,
                    category=category,
                )

                if not guard_result.approved:
                    logger.info(
                        "SafeExecutor REJECTED opp=%d: %s",
                        opportunity_id, "; ".join(guard_result.rejection_reasons),
                    )
                    return SafeExecutionResult(
                        approved=False,
                        guard_result=guard_result,
                    )

                # Create and open position (mutates portfolio in-memory)
                position = Position(
                    opportunity_id=opportunity_id,
                    allocated_amount=guard_result.final_allocation,
                    entry_price=capital_required,
                    expected_profit=expected_profit,
                    expected_roi=expected_roi,
                    risk_score=risk_score,
                    product_id=product_id,
                    marketplace=marketplace,
                    category=category,
                )
                self._tracker.open_position(portfolio, position)

                # On context exit: portfolio is persisted + committed atomically
                # If DB write fails → exception → transaction rolls back → safe

                logger.info(
                    "SafeExecutor COMMITTED opp=%d alloc=$%.2f",
                    opportunity_id, guard_result.final_allocation,
                )
                return SafeExecutionResult(
                    approved=True,
                    guard_result=guard_result,
                    position=position,
                )

        except Exception as e:
            logger.error(
                "SafeExecutor ROLLBACK opp=%d: %s",
                opportunity_id, str(e), exc_info=True,
            )
            return SafeExecutionResult(
                approved=False,
                error=f"Transaction rolled back: {e}",
            )

    async def close_position_safe(
        self,
        opportunity_id: int,
        actual_profit: float,
    ) -> bool:
        """Atomically close a position and realize P&L."""
        try:
            async with self._store.locked_portfolio() as portfolio:
                # Verify position exists before closing
                open_pos = next(
                    (p for p in portfolio.active_positions
                     if p.opportunity_id == opportunity_id
                     and p.status == PositionStatus.OPEN),
                    None,
                )
                if open_pos is None:
                    logger.error(
                        "SafeExecutor close FAILED opp=%d: no open position found",
                        opportunity_id,
                    )
                    return False

                if actual_profit >= 0:
                    self._tracker.close_position_success(
                        portfolio, opportunity_id, actual_profit,
                    )
                else:
                    self._tracker.close_position_failure(
                        portfolio, opportunity_id, abs(actual_profit),
                    )
                # Auto-persisted on context exit
                return True
        except Exception as e:
            logger.error(
                "SafeExecutor close ROLLBACK opp=%d: %s",
                opportunity_id, str(e), exc_info=True,
            )
            return False
