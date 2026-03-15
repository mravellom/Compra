"""Command Pattern for trade execution.

Each command encapsulates a trade action with execute/validate logic.
Commands are dispatched by the ExecutionService.
"""
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from .enums import OrderType
from .models import ExecutionResult, TradeOrder

logger = logging.getLogger(__name__)


class TradeCommand(ABC):
    """Abstract base for trade commands."""

    def __init__(self, order: TradeOrder) -> None:
        self.order = order

    @property
    @abstractmethod
    def command_type(self) -> OrderType:
        ...

    @abstractmethod
    def execute(self) -> ExecutionResult:
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

    In a real system, this would integrate with marketplace APIs.
    Currently acts as a validated placeholder that records intent.
    """

    @property
    def command_type(self) -> OrderType:
        return OrderType.BUY

    def execute(self) -> ExecutionResult:
        valid, reason = self.validate()
        if not valid:
            return ExecutionResult(
                order_id=self.order.id or 0,
                success=False,
                error_message=reason,
            )

        # Marketplace API integration point
        # In production: call marketplace SDK to place purchase order
        logger.info(
            "BUY executed: product=%d marketplace=%s price=%.2f qty=%d",
            self.order.product_id, self.order.marketplace,
            self.order.price, self.order.quantity,
        )

        return ExecutionResult(
            order_id=self.order.id or 0,
            success=True,
            executed_price=self.order.price,
            fees=0.0,  # would come from marketplace API response
            metadata={
                "marketplace": self.order.marketplace,
                "executed_at": datetime.now(timezone.utc).isoformat(),
            },
        )


class SellCommand(TradeCommand):
    """Execute a sell/list order on the target marketplace."""

    @property
    def command_type(self) -> OrderType:
        return OrderType.SELL

    def execute(self) -> ExecutionResult:
        valid, reason = self.validate()
        if not valid:
            return ExecutionResult(
                order_id=self.order.id or 0,
                success=False,
                error_message=reason,
            )

        logger.info(
            "SELL executed: product=%d marketplace=%s price=%.2f qty=%d",
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
            },
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

    def execute(self) -> ExecutionResult:
        valid, reason = self.validate()
        if not valid:
            return ExecutionResult(
                order_id=self.order.id or 0,
                success=False,
                error_message=reason,
            )

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
    OrderType.LIST_ITEM: SellCommand,  # list_item uses same logic as sell
}


def create_command(order: TradeOrder) -> TradeCommand:
    """Factory: create the appropriate command for an order type."""
    command_cls = COMMAND_REGISTRY.get(order.order_type)
    if command_cls is None:
        raise ValueError(f"Unknown order type: {order.order_type}")
    return command_cls(order)
