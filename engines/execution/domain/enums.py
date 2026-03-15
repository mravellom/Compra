from enum import Enum


class OrderStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OrderType(str, Enum):
    BUY = "buy"
    SELL = "sell"
    LIST_ITEM = "list_item"


class ApprovalState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_APPROVED = "auto_approved"


class ExecutionMode(str, Enum):
    MANUAL = "manual"
    ASSISTED = "assisted"
    AUTO = "auto"


# Valid status transitions
VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.DRAFT: {OrderStatus.PENDING_APPROVAL, OrderStatus.CANCELLED},
    OrderStatus.PENDING_APPROVAL: {OrderStatus.APPROVED, OrderStatus.CANCELLED},
    OrderStatus.APPROVED: {OrderStatus.EXECUTING, OrderStatus.CANCELLED},
    OrderStatus.EXECUTING: {OrderStatus.EXECUTED, OrderStatus.FAILED},
    OrderStatus.EXECUTED: set(),
    OrderStatus.FAILED: {OrderStatus.DRAFT},  # retry
    OrderStatus.CANCELLED: set(),
}
