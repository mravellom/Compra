"""Command Pattern for trade execution.

Each command encapsulates a trade action with execute/validate logic.
Commands are dispatched by the ExecutionService.

Now supports REAL marketplace execution via MarketplaceClient.
Falls back to simulation mode if no client is configured.
"""
import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from .enums import OrderType
from .models import ExecutionResult, TradeOrder

logger = logging.getLogger(__name__)


def _get_marketplace_client(marketplace: str):
    """Lazy import to avoid circular dependencies."""
    from ..infrastructure.marketplace_factory import get_marketplace_client
    return get_marketplace_client(marketplace)


class TradeCommand(ABC):
    """Abstract base for trade commands."""

    def __init__(self, order: TradeOrder) -> None:
        self.order = order

    @property
    @abstractmethod
    def command_type(self) -> OrderType:
        ...

    @abstractmethod
    async def execute(self) -> ExecutionResult:
        """Execute the trade action."""
        ...

    def validate(self) -> tuple[bool, str]:
        """Pre-execution validation. Returns (is_valid, reason)."""
        if self.order.price <= 0:
            return False, "Price must be positive"
        if self.order.quantity <= 0:
            return False, "Quantity must be positive"
        return True, ""


class BuyCommand(TradeCommand):
    """Execute a buy order on the source marketplace.

    Integrates with real marketplace APIs when configured.
    Falls back to simulation mode (logging only) if no client is available.
    """

    @property
    def command_type(self) -> OrderType:
        return OrderType.BUY

    async def execute(self) -> ExecutionResult:
        valid, reason = self.validate()
        if not valid:
            return ExecutionResult(
                order_id=self.order.id or 0,
                success=False,
                error_message=reason,
            )

        client = _get_marketplace_client(self.order.marketplace)

        if client is None:
            # Simulation mode — no marketplace client configured
            logger.warning(
                "SIMULATION BUY: product=%d marketplace=%s price=%.2f qty=%d "
                "(no marketplace client configured)",
                self.order.product_id, self.order.marketplace,
                self.order.price, self.order.quantity,
            )
            return ExecutionResult(
                order_id=self.order.id or 0,
                success=True,
                executed_price=self.order.price,
                fees=0.0,
                metadata={
                    "marketplace": self.order.marketplace,
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                    "mode": "simulation",
                },
            )

        # Real marketplace execution
        listing_url = self.order.risk_assessment.get("listing_url", "")
        if not listing_url:
            # Try to find listing URL from metadata
            listing_url = str(self.order.risk_assessment.get("buy_url", ""))

        logger.info(
            "REAL BUY: product=%d marketplace=%s price=%.2f qty=%d url=%s",
            self.order.product_id, self.order.marketplace,
            self.order.price, self.order.quantity, listing_url[:80],
        )

        try:
            result = await client.place_buy_order(
                listing_url=listing_url,
                quantity=self.order.quantity,
                max_price=self.order.price * 1.05,  # Allow 5% slippage
            )

            if result.success:
                logger.info(
                    "BUY SUCCESS: order_id=%s price=%.2f fees=%.2f",
                    result.marketplace_order_id, result.executed_price, result.fees,
                )
            else:
                logger.warning(
                    "BUY FAILED: %s", result.error_message,
                )

            return ExecutionResult(
                order_id=self.order.id or 0,
                success=result.success,
                executed_price=result.executed_price,
                fees=result.fees,
                error_message=result.error_message,
                metadata={
                    "marketplace": self.order.marketplace,
                    "marketplace_order_id": result.marketplace_order_id or "",
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                    "mode": "real",
                    **result.metadata,
                },
            )

        except Exception as e:
            logger.error(
                "BUY ERROR: product=%d marketplace=%s error=%s",
                self.order.product_id, self.order.marketplace, str(e),
                exc_info=True,
            )
            return ExecutionResult(
                order_id=self.order.id or 0,
                success=False,
                error_message=f"Marketplace API error: {str(e)}",
                metadata={"marketplace": self.order.marketplace, "mode": "real"},
            )


class SellCommand(TradeCommand):
    """Execute a sell/list order on the target marketplace.

    Creates a real listing when marketplace client is configured.
    """

    @property
    def command_type(self) -> OrderType:
        return OrderType.SELL

    async def execute(self) -> ExecutionResult:
        valid, reason = self.validate()
        if not valid:
            return ExecutionResult(
                order_id=self.order.id or 0,
                success=False,
                error_message=reason,
            )

        client = _get_marketplace_client(self.order.marketplace)

        if client is None:
            logger.warning(
                "SIMULATION SELL: product=%d marketplace=%s price=%.2f qty=%d "
                "(no marketplace client configured)",
                self.order.product_id, self.order.marketplace,
                self.order.price, self.order.quantity,
            )
            return ExecutionResult(
                order_id=self.order.id or 0,
                success=True,
                executed_price=self.order.price,
                fees=0.0,
                metadata={
                    "marketplace": self.order.marketplace,
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                    "mode": "simulation",
                },
            )

        # Real listing creation
        title = self.order.risk_assessment.get("product_title", f"Product {self.order.product_id}")
        description = self.order.risk_assessment.get("description", "")
        images = self.order.risk_assessment.get("images", [])
        category_id = self.order.risk_assessment.get("category_id")

        logger.info(
            "REAL SELL: product=%d marketplace=%s price=%.2f qty=%d title=%s",
            self.order.product_id, self.order.marketplace,
            self.order.price, self.order.quantity, title[:50],
        )

        try:
            result = await client.place_sell_listing(
                title=title,
                price=self.order.price,
                quantity=self.order.quantity,
                category_id=category_id,
                description=description,
                images=images,
            )

            if result.success:
                logger.info(
                    "SELL SUCCESS: listing_id=%s price=%.2f fees=%.2f",
                    result.marketplace_order_id, result.executed_price, result.fees,
                )
            else:
                logger.warning("SELL FAILED: %s", result.error_message)

            return ExecutionResult(
                order_id=self.order.id or 0,
                success=result.success,
                executed_price=result.executed_price,
                fees=result.fees,
                error_message=result.error_message,
                metadata={
                    "marketplace": self.order.marketplace,
                    "marketplace_order_id": result.marketplace_order_id or "",
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                    "mode": "real",
                    **result.metadata,
                },
            )

        except Exception as e:
            logger.error(
                "SELL ERROR: product=%d marketplace=%s error=%s",
                self.order.product_id, self.order.marketplace, str(e),
                exc_info=True,
            )
            return ExecutionResult(
                order_id=self.order.id or 0,
                success=False,
                error_message=f"Marketplace API error: {str(e)}",
                metadata={"marketplace": self.order.marketplace, "mode": "real"},
            )


class CancelCommand(TradeCommand):
    """Cancel a pending or approved order."""

    @property
    def command_type(self) -> OrderType:
        return OrderType.BUY  # cancellation applies to the original type

    def validate(self) -> tuple[bool, str]:
        from .enums import OrderStatus
        if self.order.status in (OrderStatus.EXECUTED, OrderStatus.CANCELLED):
            return False, f"Cannot cancel order in {self.order.status.value} state"
        return True, ""

    async def execute(self) -> ExecutionResult:
        valid, reason = self.validate()
        if not valid:
            return ExecutionResult(
                order_id=self.order.id or 0,
                success=False,
                error_message=reason,
            )

        # Try real cancellation if marketplace order exists
        marketplace_order_id = self.order.risk_assessment.get("marketplace_order_id")
        if marketplace_order_id:
            client = _get_marketplace_client(self.order.marketplace)
            if client:
                try:
                    cancelled = await client.cancel_order(marketplace_order_id)
                    if not cancelled:
                        logger.warning("Marketplace cancel failed for %s", marketplace_order_id)
                except Exception as e:
                    logger.error("Cancel error: %s", e)

        logger.info("Order %d cancelled", self.order.id or 0)

        return ExecutionResult(
            order_id=self.order.id or 0,
            success=True,
            metadata={"action": "cancelled", "previous_status": self.order.status.value},
        )


# Command factory
COMMAND_REGISTRY: dict[OrderType, type[TradeCommand]] = {
    OrderType.BUY: BuyCommand,
    OrderType.SELL: SellCommand,
    OrderType.LIST_ITEM: SellCommand,
}


def create_command(order: TradeOrder) -> TradeCommand:
    """Factory: create the appropriate command for an order type."""
    command_cls = COMMAND_REGISTRY.get(order.order_type)
    if command_cls is None:
        raise ValueError(f"Unknown order type: {order.order_type}")
    return command_cls(order)
