"""Execution service — orchestrates the full order lifecycle."""
import logging
from datetime import datetime, timezone
from typing import Optional

from ..domain.commands import create_command, CancelCommand
from ..domain.enums import ApprovalState, ExecutionMode, OrderStatus, OrderType
from ..domain.models import ExecutionResult, PortfolioSummary, RiskAssessment, TradeOrder
from ..infrastructure.execution_repository import ExecutionRepository
from ..infrastructure.redis_publisher import ExecutionEventPublisher
from .approval_workflow import ApprovalWorkflow
from .risk_engine import RiskEngine

logger = logging.getLogger(__name__)


class ExecutionService:
    """Application service for the execution engine.

    Coordinates: risk assessment -> approval -> command execution -> persistence.
    """

    def __init__(
        self,
        repository: ExecutionRepository,
        risk_engine: RiskEngine,
        approval_workflow: ApprovalWorkflow,
        publisher: Optional[ExecutionEventPublisher] = None,
    ) -> None:
        self._repo = repository
        self._risk = risk_engine
        self._approval = approval_workflow
        self._publisher = publisher

    async def create_order(
        self,
        product_id: int,
        order_type: OrderType,
        marketplace: str,
        price: float,
        quantity: int = 1,
        opportunity_id: Optional[int] = None,
        estimated_profit: float = 0,
        execution_mode: ExecutionMode = ExecutionMode.MANUAL,
    ) -> TradeOrder:
        """Create a new trade order with risk assessment."""
        order = TradeOrder(
            product_id=product_id,
            opportunity_id=opportunity_id,
            order_type=order_type,
            marketplace=marketplace,
            price=price,
            quantity=quantity,
            total_cost=price * quantity,
            estimated_profit=estimated_profit,
            execution_mode=execution_mode,
        )

        # Run risk assessment
        portfolio = await self._repo.get_portfolio_summary()
        assessment = self._risk.assess(order, portfolio)
        order.risk_assessment = assessment.model_dump()

        if not assessment.passed:
            logger.warning(
                "Risk check BLOCKED order for product=%d: %s",
                product_id, assessment.reasons,
            )
            # Risk gate: block the order — do not allow it through approval
            order.status = OrderStatus.CANCELLED
            order.approval_state = ApprovalState.REJECTED
            order.error_message = f"Risk check failed: {'; '.join(assessment.reasons)}"

            order = await self._repo.save_order(order)
            await self._repo.log_event(
                order.id, "risk_rejected", None, order.status.value,
                {"risk_passed": False, "reasons": assessment.reasons},
            )
            return order

        # Submit through approval workflow
        order = self._approval.submit(order)

        # Persist
        order = await self._repo.save_order(order)
        await self._repo.log_event(
            order.id, "created", None, order.status.value,  # type: ignore[arg-type]
            {"risk_passed": assessment.passed, "mode": execution_mode.value},
        )

        # Publish event
        if self._publisher:
            await self._publisher.publish("order_created", {
                "order_id": order.id,
                "product_id": product_id,
                "order_type": order_type.value,
                "status": order.status.value,
            })

        return order

    async def approve_order(
        self, order_id: int, approved_by: str = "user"
    ) -> TradeOrder:
        """Manually approve an order."""
        order = await self._repo.get_order(order_id)
        if order is None:
            raise ValueError(f"Order {order_id} not found")

        old_status = order.status.value
        order = self._approval.approve(order, approved_by)
        await self._repo.update_order(order)
        await self._repo.log_event(
            order_id, "approved", old_status, order.status.value,
            {"approved_by": approved_by},
        )
        return order

    async def reject_order(
        self, order_id: int, reason: str = "", rejected_by: str = "user"
    ) -> TradeOrder:
        """Reject an order."""
        order = await self._repo.get_order(order_id)
        if order is None:
            raise ValueError(f"Order {order_id} not found")

        old_status = order.status.value
        order = self._approval.reject(order, reason, rejected_by)
        await self._repo.update_order(order)
        await self._repo.log_event(
            order_id, "rejected", old_status, order.status.value,
            {"reason": reason, "rejected_by": rejected_by},
        )
        return order

    async def execute_order(self, order_id: int) -> ExecutionResult:
        """Execute an approved order."""
        order = await self._repo.get_order(order_id)
        if order is None:
            raise ValueError(f"Order {order_id} not found")
        if order.status != OrderStatus.APPROVED:
            raise ValueError(f"Order {order_id} must be approved before execution (current: {order.status.value})")

        # Mark as executing
        old_status = order.status.value
        order.status = OrderStatus.EXECUTING
        await self._repo.update_order(order)
        await self._repo.log_event(order_id, "executing", old_status, "executing")

        # Create and run command
        command = create_command(order)
        result = command.execute()

        # Update based on result
        if result.success:
            order.status = OrderStatus.EXECUTED
            order.executed_at = datetime.now(timezone.utc)
        else:
            order.status = OrderStatus.FAILED
            order.error_message = result.error_message

        await self._repo.update_order(order)
        await self._repo.log_event(
            order_id, "executed" if result.success else "failed",
            "executing", order.status.value,
            {"success": result.success, "error": result.error_message},
        )

        # Publish
        if self._publisher:
            await self._publisher.publish("order_executed", {
                "order_id": order_id,
                "success": result.success,
                "status": order.status.value,
            })

        return result

    async def cancel_order(self, order_id: int) -> TradeOrder:
        """Cancel a pending or approved order."""
        order = await self._repo.get_order(order_id)
        if order is None:
            raise ValueError(f"Order {order_id} not found")

        cancel_cmd = CancelCommand(order)
        result = cancel_cmd.execute()

        if not result.success:
            raise ValueError(result.error_message or "Cannot cancel order")

        old_status = order.status.value
        order.status = OrderStatus.CANCELLED
        await self._repo.update_order(order)
        await self._repo.log_event(
            order_id, "cancelled", old_status, "cancelled",
        )
        return order

    async def get_order(self, order_id: int) -> Optional[TradeOrder]:
        return await self._repo.get_order(order_id)

    async def list_orders(
        self, status: Optional[str] = None, limit: int = 50
    ) -> list[TradeOrder]:
        return await self._repo.list_orders(status, limit)

    async def get_portfolio(self) -> PortfolioSummary:
        return await self._repo.get_portfolio_summary()

    async def get_risk_assessment(
        self, product_id: int, price: float, quantity: int = 1
    ) -> RiskAssessment:
        """Pre-check risk for a potential order without creating it."""
        order = TradeOrder(
            product_id=product_id,
            order_type=OrderType.BUY,
            marketplace="",
            price=price,
            quantity=quantity,
            total_cost=price * quantity,
        )
        portfolio = await self._repo.get_portfolio_summary()
        return self._risk.assess(order, portfolio)
