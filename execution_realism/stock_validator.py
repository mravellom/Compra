"""
Stock Validator — checks that the product is still available before buying.

Out-of-stock = immediate reject.
Low stock = reduced confidence (race condition with other buyers).
Frequent stockout history = seller unreliability penalty.
"""
import logging

from .config import StockValidationConfig
from .models import StockValidationResult

logger = logging.getLogger(__name__)


class StockValidator:
    """Validates stock availability and adjusts confidence."""

    def __init__(self, config: StockValidationConfig | None = None) -> None:
        self._cfg = config or StockValidationConfig()

    def check(
        self,
        stock_available: int | None,
        seller_stockout_count: int = 0,
    ) -> StockValidationResult:
        """Check stock level and apply confidence adjustments.

        Args:
            stock_available: Current stock count. None = unknown (scraped pages
                             often don't expose exact stock).
            seller_stockout_count: Historical count of stockouts for this seller
                                   in the evaluation window.

        Returns:
            StockValidationResult with availability and confidence_adjustment.
        """
        confidence_adjustment = 1.0

        # Unknown stock: cautious — slight penalty, but allow
        if stock_available is None:
            # Can't confirm, assume available with penalty
            confidence_adjustment *= 0.90

            if seller_stockout_count >= self._cfg.frequent_stockout_threshold:
                confidence_adjustment *= (1.0 - self._cfg.frequent_stockout_penalty)

            return StockValidationResult(
                available=True,
                stock_level=None,
                confidence_adjustment=round(confidence_adjustment, 4),
                reason="stock unknown — reduced confidence",
            )

        # Out of stock
        if stock_available <= 0:
            logger.info("Stock REJECTED: out of stock")
            return StockValidationResult(
                available=False,
                stock_level=0,
                confidence_adjustment=0.0,
                reason="out of stock",
            )

        # Low stock
        if stock_available <= self._cfg.low_stock_threshold:
            confidence_adjustment *= (1.0 - self._cfg.low_stock_confidence_penalty)
            logger.debug("Stock LOW: %d units, confidence ×%.2f",
                         stock_available, confidence_adjustment)

        # Frequent stockout seller
        if seller_stockout_count >= self._cfg.frequent_stockout_threshold:
            confidence_adjustment *= (1.0 - self._cfg.frequent_stockout_penalty)
            logger.debug("Seller stockout penalty: %d stockouts, confidence ×%.2f",
                         seller_stockout_count, confidence_adjustment)

        return StockValidationResult(
            available=True,
            stock_level=stock_available,
            confidence_adjustment=round(confidence_adjustment, 4),
        )
