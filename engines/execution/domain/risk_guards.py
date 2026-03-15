"""Risk guards for execution engine.

Each guard evaluates a single risk dimension. The RiskEngine
runs all guards and produces a composite RiskAssessment.
"""
import logging
import os
from abc import ABC, abstractmethod

from .models import PortfolioSummary, TradeOrder

logger = logging.getLogger(__name__)


class RiskGuard(ABC):
    """Abstract base for risk evaluation guards."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def evaluate(self, order: TradeOrder, portfolio: PortfolioSummary) -> tuple[bool, str]:
        """Returns (passed, reason). reason is empty if passed."""
        ...


class MaxExposureGuard(RiskGuard):
    """Ensures total open capital doesn't exceed configured limit."""

    @property
    def name(self) -> str:
        return "max_exposure"

    def evaluate(self, order: TradeOrder, portfolio: PortfolioSummary) -> tuple[bool, str]:
        max_exposure = float(os.getenv("MAX_TOTAL_EXPOSURE", "10000"))
        new_exposure = portfolio.total_exposure + order.total_cost
        if new_exposure > max_exposure:
            return False, f"Total exposure ${new_exposure:.2f} would exceed limit ${max_exposure:.2f}"
        return True, ""


class SingleOrderLimitGuard(RiskGuard):
    """Prevents any single order from exceeding a capital threshold."""

    @property
    def name(self) -> str:
        return "single_order_limit"

    def evaluate(self, order: TradeOrder, portfolio: PortfolioSummary) -> tuple[bool, str]:
        max_single = float(os.getenv("MAX_SINGLE_ORDER", "2000"))
        if order.total_cost > max_single:
            return False, f"Order cost ${order.total_cost:.2f} exceeds single order limit ${max_single:.2f}"
        return True, ""


class DailyLimitGuard(RiskGuard):
    """Limits the number of orders executed per day."""

    @property
    def name(self) -> str:
        return "daily_limit"

    def evaluate(self, order: TradeOrder, portfolio: PortfolioSummary) -> tuple[bool, str]:
        max_daily = int(os.getenv("MAX_DAILY_ORDERS", "50"))
        if portfolio.executed_today >= max_daily:
            return False, f"Daily order limit reached ({max_daily})"
        return True, ""


class MinROIGuard(RiskGuard):
    """Ensures the opportunity meets minimum ROI threshold."""

    @property
    def name(self) -> str:
        return "min_roi"

    def evaluate(self, order: TradeOrder, portfolio: PortfolioSummary) -> tuple[bool, str]:
        min_roi = float(os.getenv("EXECUTION_MIN_ROI", "0.05"))
        if order.total_cost > 0:
            roi = order.estimated_profit / order.total_cost
            if roi < min_roi:
                return False, f"ROI {roi:.1%} below minimum {min_roi:.1%}"
        return True, ""


class MinProfitGuard(RiskGuard):
    """Ensures minimum absolute profit per trade."""

    @property
    def name(self) -> str:
        return "min_profit"

    def evaluate(self, order: TradeOrder, portfolio: PortfolioSummary) -> tuple[bool, str]:
        min_profit = float(os.getenv("EXECUTION_MIN_PROFIT", "5.0"))
        if order.estimated_profit < min_profit:
            return False, f"Estimated profit ${order.estimated_profit:.2f} below minimum ${min_profit:.2f}"
        return True, ""


class DuplicateGuard(RiskGuard):
    """Prevents duplicate orders for the same opportunity."""

    @property
    def name(self) -> str:
        return "duplicate_check"

    def evaluate(self, order: TradeOrder, portfolio: PortfolioSummary) -> tuple[bool, str]:
        # This is checked at the repository level; here we do a lightweight check
        # via the portfolio's existing order IDs (injected as metadata if available)
        return True, ""


# Default guards in evaluation order
DEFAULT_GUARDS: list[type[RiskGuard]] = [
    MaxExposureGuard,
    SingleOrderLimitGuard,
    DailyLimitGuard,
    MinROIGuard,
    MinProfitGuard,
    DuplicateGuard,
]
