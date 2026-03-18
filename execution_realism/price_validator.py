"""
Real-Time Price Validator — re-checks price before committing capital.

The price we saw during detection may have changed. If the buy price moved up
more than the threshold, the opportunity is no longer valid.
"""
import logging

from .config import PriceValidationConfig
from .models import PriceValidationResult

logger = logging.getLogger(__name__)


class PriceValidator:
    """Validates that current price hasn't deviated from expected."""

    def __init__(self, config: PriceValidationConfig | None = None) -> None:
        self._cfg = config or PriceValidationConfig()

    def validate(
        self,
        expected_buy_price: float,
        current_buy_price: float,
        expected_sell_price: float,
        total_fees: float,
    ) -> PriceValidationResult:
        """Compare expected vs current buy price.

        Args:
            expected_buy_price: Price at detection time.
            current_buy_price: Price right now (from re-query or snapshot).
            expected_sell_price: Sell price (assumed stable for this check).
            total_fees: All fees already computed.

        Returns:
            PriceValidationResult with validity and updated profit.
        """
        if expected_buy_price <= 0:
            return PriceValidationResult(
                is_valid=False,
                expected_buy_price=expected_buy_price,
                current_buy_price=current_buy_price,
                price_delta_pct=0.0,
                original_profit=0.0,
                updated_profit=0.0,
                reason="invalid expected_buy_price <= 0",
            )

        delta_pct = (current_buy_price - expected_buy_price) / expected_buy_price
        original_profit = expected_sell_price - expected_buy_price - total_fees
        updated_profit = expected_sell_price - current_buy_price - total_fees

        # Check 1: Price deviation too large (either direction)
        if abs(delta_pct) > self._cfg.max_price_deviation_pct:
            reason = (
                f"price deviation {delta_pct:+.1%} exceeds "
                f"±{self._cfg.max_price_deviation_pct:.0%} threshold"
            )
            logger.info("Price REJECTED: %s (expected=$%.2f current=$%.2f)",
                        reason, expected_buy_price, current_buy_price)
            return PriceValidationResult(
                is_valid=False,
                expected_buy_price=expected_buy_price,
                current_buy_price=current_buy_price,
                price_delta_pct=round(delta_pct, 4),
                original_profit=round(original_profit, 2),
                updated_profit=round(updated_profit, 2),
                reason=reason,
            )

        # Check 2: Updated profit still viable
        if updated_profit < self._cfg.min_profit_after_recheck_usd:
            reason = (
                f"updated profit ${updated_profit:.2f} below "
                f"minimum ${self._cfg.min_profit_after_recheck_usd:.2f}"
            )
            logger.info("Price REJECTED: %s", reason)
            return PriceValidationResult(
                is_valid=False,
                expected_buy_price=expected_buy_price,
                current_buy_price=current_buy_price,
                price_delta_pct=round(delta_pct, 4),
                original_profit=round(original_profit, 2),
                updated_profit=round(updated_profit, 2),
                reason=reason,
            )

        logger.debug(
            "Price VALID: delta=%+.1%% profit=$%.2f→$%.2f",
            delta_pct * 100, original_profit, updated_profit,
        )

        return PriceValidationResult(
            is_valid=True,
            expected_buy_price=expected_buy_price,
            current_buy_price=current_buy_price,
            price_delta_pct=round(delta_pct, 4),
            original_profit=round(original_profit, 2),
            updated_profit=round(updated_profit, 2),
        )
