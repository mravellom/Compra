"""Risk evaluation engine — runs all guards and produces a composite assessment."""
import logging

from ..domain.models import PortfolioSummary, RiskAssessment, TradeOrder
from ..domain.risk_guards import DEFAULT_GUARDS, RiskGuard

logger = logging.getLogger(__name__)


class RiskEngine:
    """Evaluates trade orders against configurable risk guards."""

    def __init__(self, guards: list[RiskGuard] | None = None) -> None:
        if guards is None:
            guards = [cls() for cls in DEFAULT_GUARDS]
        self._guards = guards

    def assess(self, order: TradeOrder, portfolio: PortfolioSummary) -> RiskAssessment:
        """Run all guards and return composite assessment."""
        assessment = RiskAssessment(
            total_exposure=portfolio.total_exposure + order.total_cost,
            daily_trade_count=portfolio.executed_today,
        )

        for guard in self._guards:
            try:
                passed, reason = guard.evaluate(order, portfolio)
                if passed:
                    assessment.guards_passed.append(guard.name)
                else:
                    assessment.guards_failed.append(guard.name)
                    assessment.reasons.append(reason)
                    logger.info(
                        "Guard %s failed for order product=%d: %s",
                        guard.name, order.product_id, reason,
                    )
            except Exception:
                logger.error("Guard %s error", guard.name, exc_info=True)
                assessment.guards_failed.append(guard.name)
                assessment.reasons.append(f"Guard {guard.name} error")

        assessment.passed = len(assessment.guards_failed) == 0
        return assessment

    def add_guard(self, guard: RiskGuard) -> None:
        self._guards.append(guard)
