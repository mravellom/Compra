"""
Slippage Model — estimates execution price deviation from expected.

In practice, you rarely buy at the exact listed price:
- High-volatility products move fast
- Popular items get bid up
- Cart-to-checkout delays cause drift

Slippage increases the effective buy price, reducing profit.
"""
import logging

from .config import SlippageConfig
from .models import SlippageResult

logger = logging.getLogger(__name__)


class SlippageModel:
    """Estimates price slippage and validates post-slippage profitability."""

    def __init__(self, config: SlippageConfig | None = None) -> None:
        self._cfg = config or SlippageConfig()

    def estimate(
        self,
        expected_buy_price: float,
        expected_sell_price: float,
        total_fees: float,
        price_volatility: float,
        estimated_monthly_sales: float,
    ) -> SlippageResult:
        """Estimate slippage and compute post-slippage profit.

        Args:
            expected_buy_price: The buy price we planned on.
            expected_sell_price: The sell price.
            total_fees: All marketplace/shipping/tax fees.
            price_volatility: Coefficient of variation of recent prices (0-1).
                              0 = perfectly stable, 0.3+ = very volatile.
            estimated_monthly_sales: Sales velocity (proxy for demand pressure).

        Returns:
            SlippageResult with slipped price and viability.
        """
        cfg = self._cfg

        # Base slippage (always present — checkout delay, rounding, etc.)
        slippage = cfg.base_slippage_pct

        # Volatility component: volatile products slip more
        # CV 0.1 → +1.5%, CV 0.3 → +4.5% (with default 2.5x multiplier)
        volatility_slippage = price_volatility * cfg.base_slippage_pct * cfg.high_volatility_multiplier
        slippage += volatility_slippage

        # Demand component: popular items get competed away
        # 30 sales/month → +1.5% (with default 1.5x multiplier)
        demand_fraction = min(1.0, estimated_monthly_sales / 30.0)
        demand_slippage = demand_fraction * cfg.base_slippage_pct * cfg.high_demand_multiplier
        slippage += demand_slippage

        # Apply slippage to buy price
        slipped_buy = expected_buy_price * (1.0 + slippage)
        profit_after = expected_sell_price - slipped_buy - total_fees

        is_viable = profit_after >= cfg.min_profit_after_slippage_usd
        reason = ""
        if not is_viable:
            reason = (
                f"profit after slippage ${profit_after:.2f} < "
                f"${cfg.min_profit_after_slippage_usd:.2f}"
            )
            logger.info("Slippage REJECTED: %s (slippage=%.1f%%)", reason, slippage * 100)

        return SlippageResult(
            estimated_slippage_pct=round(slippage, 4),
            slipped_buy_price=round(slipped_buy, 2),
            profit_after_slippage=round(profit_after, 2),
            is_viable=is_viable,
            reason=reason,
        )
