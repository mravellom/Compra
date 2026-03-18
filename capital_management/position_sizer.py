"""
Position Sizing Engine — determines how much capital to allocate per trade.

Steps:
1. Base allocation from opportunity score vs total capital
2. Risk adjustment (higher risk → smaller position)
3. Cap enforcement (per-trade, per-category, available capital)
4. Minimum viability check

Conservative by default: when in doubt, allocate less.
"""
import logging

from .config import CapitalConfig
from .models import PortfolioState, Position, PositionStatus, SizingResult

logger = logging.getLogger(__name__)


class PositionSizer:
    """Computes position size for an opportunity."""

    def __init__(self, config: CapitalConfig) -> None:
        self._cfg = config.position_sizing
        self._div_cfg = config.diversification

    def size_position(
        self,
        opportunity_score: float,
        risk_score: float,
        capital_required: float,
        portfolio: PortfolioState,
        category: str = "",
        risk_adjustment_override: float | None = None,
    ) -> SizingResult:
        """Compute allocation for a single opportunity.

        Args:
            opportunity_score: 0-100 composite quality score.
            risk_score: 0-100 risk assessment (higher = riskier).
            capital_required: Minimum capital needed (buy price * qty).
            portfolio: Current portfolio state.
            category: Product category for per-category caps.
            risk_adjustment_override: External multiplier (e.g. from RiskManager).

        Returns:
            SizingResult with allocation amount and validity.
        """
        cfg = self._cfg

        # Step 1: Base allocation — proportional to score
        # Scale: score 0→0%, score 100→max_allocation_pct
        score_fraction = max(0.0, min(1.0, opportunity_score / 100.0))
        base_allocation = portfolio.total_capital * cfg.max_allocation_pct_per_trade * score_fraction

        if base_allocation <= 0:
            return SizingResult(
                allocation=0, is_valid=False,
                reason="zero base allocation (score too low)",
            )

        # Step 2: Risk adjustment — higher risk → smaller position
        risk_fraction = max(0.0, min(1.0, risk_score / 100.0))
        risk_multiplier = 1.0 - (risk_fraction * cfg.risk_adjustment_factor)
        risk_adjusted = base_allocation * risk_multiplier

        # Apply external risk adjustment (e.g. from consecutive losses)
        if risk_adjustment_override is not None:
            risk_adjusted *= risk_adjustment_override

        # Step 3: Cap enforcement
        # 3a. Per-trade cap
        max_per_trade = portfolio.total_capital * cfg.max_allocation_pct_per_trade
        capped = min(risk_adjusted, max_per_trade)

        # 3b. Per-category cap
        if category:
            category_allocated = sum(
                p.allocated_amount for p in portfolio.active_positions
                if p.status == PositionStatus.OPEN and p.category == category
            )
            max_category = portfolio.total_capital * cfg.max_allocation_pct_per_category
            category_headroom = max(0.0, max_category - category_allocated)
            capped = min(capped, category_headroom)

        # 3c. Available capital cap
        capped = min(capped, portfolio.available_capital)

        # 3d. Must cover capital required
        if capped < capital_required and capital_required <= portfolio.available_capital:
            # If we have enough capital but sizing is below requirement,
            # try using capital_required as allocation (still subject to caps)
            if capital_required <= max_per_trade:
                capped = capital_required
            else:
                return SizingResult(
                    allocation=0, is_valid=False,
                    reason=f"capital_required ${capital_required:.2f} exceeds per-trade cap ${max_per_trade:.2f}",
                    base_allocation=round(base_allocation, 2),
                    risk_adjusted_allocation=round(risk_adjusted, 2),
                )

        # Step 4: Minimum viability
        if capped < cfg.min_allocation_usd:
            return SizingResult(
                allocation=round(capped, 2), is_valid=False,
                reason=f"allocation ${capped:.2f} below minimum ${cfg.min_allocation_usd:.2f}",
                base_allocation=round(base_allocation, 2),
                risk_adjusted_allocation=round(risk_adjusted, 2),
            )

        final = round(capped, 2)

        logger.debug(
            "Position sized: score=%.0f risk=%.0f base=$%.2f risk_adj=$%.2f final=$%.2f",
            opportunity_score, risk_score, base_allocation, risk_adjusted, final,
        )

        return SizingResult(
            allocation=final,
            is_valid=True,
            base_allocation=round(base_allocation, 2),
            risk_adjusted_allocation=round(risk_adjusted, 2),
        )
