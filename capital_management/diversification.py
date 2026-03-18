"""
Portfolio Diversification — prevents concentration risk.

Checks that adding a new position won't breach:
- per-product limits
- per-marketplace limits
- per-category limits
- total open position cap
"""
import logging

from .config import CapitalConfig
from .models import DiversificationResult, PortfolioState, Position, PositionStatus

logger = logging.getLogger(__name__)


class DiversificationGuard:
    """Checks concentration limits before allowing a new position."""

    def __init__(self, config: CapitalConfig) -> None:
        self._cfg = config.diversification

    def check(
        self,
        position: Position,
        portfolio: PortfolioState,
    ) -> DiversificationResult:
        """Check if adding this position would breach diversification limits.

        All checks run (no short-circuit) so all violations are reported.
        """
        violations: list[str] = []
        cfg = self._cfg

        open_positions = [
            p for p in portfolio.active_positions
            if p.status == PositionStatus.OPEN
        ]

        # 1. Max open positions
        if len(open_positions) >= cfg.max_open_positions:
            violations.append(
                f"max_open_positions: {len(open_positions)} >= {cfg.max_open_positions}"
            )

        total_allocated = portfolio.allocated_capital
        new_total = total_allocated + position.allocated_amount

        # Prevent division by zero — use total_capital as denominator
        denom = max(portfolio.total_capital, 1.0)

        # 2. Per-product concentration
        if position.product_id is not None:
            product_allocated = sum(
                p.allocated_amount for p in open_positions
                if p.product_id == position.product_id
            )
            product_total = product_allocated + position.allocated_amount
            product_pct = product_total / denom
            if product_pct > cfg.max_pct_per_product:
                violations.append(
                    f"product_concentration: {product_pct:.1%} > {cfg.max_pct_per_product:.0%} "
                    f"(product_id={position.product_id})"
                )

        # 3. Per-marketplace concentration
        if position.marketplace:
            mp_allocated = sum(
                p.allocated_amount for p in open_positions
                if p.marketplace == position.marketplace
            )
            mp_total = mp_allocated + position.allocated_amount
            mp_pct = mp_total / denom
            if mp_pct > cfg.max_pct_per_marketplace:
                violations.append(
                    f"marketplace_concentration: {mp_pct:.1%} > {cfg.max_pct_per_marketplace:.0%} "
                    f"(marketplace={position.marketplace})"
                )

        # 4. Per-category concentration
        if position.category:
            cat_allocated = sum(
                p.allocated_amount for p in open_positions
                if p.category == position.category
            )
            cat_total = cat_allocated + position.allocated_amount
            cat_pct = cat_total / denom
            if cat_pct > cfg.max_pct_per_category:
                violations.append(
                    f"category_concentration: {cat_pct:.1%} > {cfg.max_pct_per_category:.0%} "
                    f"(category={position.category})"
                )

        is_safe = len(violations) == 0

        if not is_safe:
            logger.info(
                "Diversification BLOCKED opp=%d: %s",
                position.opportunity_id, "; ".join(violations),
            )

        return DiversificationResult(is_safe=is_safe, violations=violations)
