"""
Execution Guard — the FINAL gate before real capital is committed.

Aggregates all checks in order:
1. RiskManager → system-level halt?
2. PositionSizer → valid allocation?
3. DiversificationGuard → concentration safe?
4. Capital available?

If ANY check fails → REJECT.
Default is REJECT. Approval requires ALL gates to pass.
"""
import logging

from .config import CapitalConfig
from .diversification import DiversificationGuard
from .models import (
    ExecutionGuardResult,
    PortfolioState,
    Position,
    PositionStatus,
    RiskCheckResult,
    RiskLevel,
)
from .position_sizer import PositionSizer
from .risk_manager import RiskManager

logger = logging.getLogger(__name__)


class ExecutionGuard:
    """Final execution gate — aggregates risk, sizing, and diversification."""

    def __init__(self, config: CapitalConfig) -> None:
        self._config = config
        self._risk_manager = RiskManager(config)
        self._sizer = PositionSizer(config)
        self._diversification = DiversificationGuard(config)

    def evaluate(
        self,
        opportunity_id: int,
        opportunity_score: float,
        risk_score: float,
        capital_required: float,
        expected_profit: float,
        expected_roi: float,
        portfolio: PortfolioState,
        product_id: int | None = None,
        marketplace: str = "",
        category: str = "",
    ) -> ExecutionGuardResult:
        """Run all gates. ALL must pass for approval.

        Order matters:
        1. Risk check first (cheapest, can short-circuit system-level halt)
        2. Position sizing
        3. Diversification
        4. Final capital availability
        """
        reasons: list[str] = []

        # ── Gate 1: Risk Manager ──────────────────────────
        risk_check = self._risk_manager.check(portfolio)
        if not risk_check.can_execute:
            reasons.extend(risk_check.reasons)
            return ExecutionGuardResult(
                approved=False,
                rejection_reasons=reasons,
                risk_check=risk_check,
            )

        # ── Gate 2: Position Sizing ───────────────────────
        sizing = self._sizer.size_position(
            opportunity_score=opportunity_score,
            risk_score=risk_score,
            capital_required=capital_required,
            portfolio=portfolio,
            category=category,
            risk_adjustment_override=risk_check.adjustment_factor if risk_check.adjustment_factor < 1.0 else None,
        )
        if not sizing.is_valid:
            reasons.append(f"sizing: {sizing.reason}")
            return ExecutionGuardResult(
                approved=False,
                rejection_reasons=reasons,
                risk_check=risk_check,
                sizing_result=sizing,
            )

        # ── Gate 3: Diversification ───────────────────────
        tentative_position = Position(
            opportunity_id=opportunity_id,
            allocated_amount=sizing.allocation,
            entry_price=capital_required,
            expected_profit=expected_profit,
            expected_roi=expected_roi,
            risk_score=risk_score,
            product_id=product_id,
            marketplace=marketplace,
            category=category,
        )
        div_check = self._diversification.check(tentative_position, portfolio)
        if not div_check.is_safe:
            reasons.extend(div_check.violations)
            return ExecutionGuardResult(
                approved=False,
                rejection_reasons=reasons,
                risk_check=risk_check,
                sizing_result=sizing,
                diversification_result=div_check,
            )

        # ── Gate 4: Final capital check ───────────────────
        if sizing.allocation > portfolio.available_capital:
            reasons.append(
                f"insufficient capital: need ${sizing.allocation:.2f}, "
                f"available ${portfolio.available_capital:.2f}"
            )
            return ExecutionGuardResult(
                approved=False,
                rejection_reasons=reasons,
                risk_check=risk_check,
                sizing_result=sizing,
                diversification_result=div_check,
            )

        # ── ALL GATES PASSED ─────────────────────────────
        logger.info(
            "ExecutionGuard APPROVED opp=%d allocation=$%.2f risk=%s",
            opportunity_id, sizing.allocation, risk_check.risk_level.value,
        )

        return ExecutionGuardResult(
            approved=True,
            final_allocation=sizing.allocation,
            risk_check=risk_check,
            sizing_result=sizing,
            diversification_result=div_check,
        )
