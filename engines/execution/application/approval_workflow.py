"""Approval workflow state machine for trade orders."""
import logging
import os
from datetime import datetime, timezone

from ..domain.enums import ApprovalState, ExecutionMode, OrderStatus, VALID_TRANSITIONS
from ..domain.models import TradeOrder

logger = logging.getLogger(__name__)


class ApprovalWorkflow:
    """Manages the approval lifecycle of trade orders.

    Rules:
    - AUTO mode: auto-approve if below thresholds
    - ASSISTED mode: always require human approval
    - MANUAL mode: human creates and executes manually
    """

    def submit(self, order: TradeOrder) -> TradeOrder:
        """Submit an order for approval. May auto-approve based on mode and thresholds."""
        if order.status != OrderStatus.DRAFT:
            raise ValueError(f"Cannot submit order in {order.status.value} state")

        if order.execution_mode == ExecutionMode.AUTO:
            if self._meets_auto_approval(order):
                order.status = OrderStatus.APPROVED
                order.approval_state = ApprovalState.AUTO_APPROVED
                order.approved_at = datetime.now(timezone.utc)
                logger.info("Order product=%d auto-approved (%.2f USD)", order.product_id, order.total_cost)
                return order

        order.status = OrderStatus.PENDING_APPROVAL
        order.approval_state = ApprovalState.PENDING
        return order

    def approve(self, order: TradeOrder, approved_by: str = "user") -> TradeOrder:
        """Manually approve an order."""
        if order.status != OrderStatus.PENDING_APPROVAL:
            raise ValueError(f"Cannot approve order in {order.status.value} state")

        order.status = OrderStatus.APPROVED
        order.approval_state = ApprovalState.APPROVED
        order.approved_by = approved_by
        order.approved_at = datetime.now(timezone.utc)
        return order

    def reject(self, order: TradeOrder, reason: str = "", rejected_by: str = "user") -> TradeOrder:
        """Reject an order."""
        if order.status != OrderStatus.PENDING_APPROVAL:
            raise ValueError(f"Cannot reject order in {order.status.value} state")

        order.status = OrderStatus.CANCELLED
        order.approval_state = ApprovalState.REJECTED
        order.approved_by = rejected_by
        order.error_message = reason or "Rejected by user"
        return order

    @staticmethod
    def validate_transition(current: OrderStatus, target: OrderStatus) -> bool:
        """Check if a status transition is valid."""
        return target in VALID_TRANSITIONS.get(current, set())

    @staticmethod
    def _meets_auto_approval(order: TradeOrder) -> bool:
        """Check if order qualifies for automatic approval."""
        max_auto_cost = float(os.getenv("AUTO_APPROVE_MAX_COST", "50"))
        min_auto_confidence = float(os.getenv("AUTO_APPROVE_MIN_CONFIDENCE", "0.7"))

        if order.total_cost > max_auto_cost:
            return False

        # Check risk assessment confidence if available
        risk = order.risk_assessment
        if isinstance(risk, dict) and risk.get("confidence", 0) < min_auto_confidence:
            return False

        return True
