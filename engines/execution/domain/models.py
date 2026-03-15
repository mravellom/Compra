from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from .enums import ApprovalState, ExecutionMode, OrderStatus, OrderType


class RiskAssessment(BaseModel):
    """Result of evaluating risk guards on an order."""
    passed: bool = False
    total_exposure: float = 0.0
    daily_trade_count: int = 0
    guards_passed: list[str] = Field(default_factory=list)
    guards_failed: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class TradeOrder(BaseModel):
    """Represents a trade execution order."""
    id: Optional[int] = None
    opportunity_id: Optional[int] = None
    product_id: int
    order_type: OrderType
    marketplace: str
    price: float
    quantity: int = 1
    total_cost: float = 0.0
    estimated_profit: float = 0.0
    status: OrderStatus = OrderStatus.DRAFT
    approval_state: ApprovalState = ApprovalState.PENDING
    execution_mode: ExecutionMode = ExecutionMode.MANUAL
    risk_assessment: dict = Field(default_factory=dict)
    error_message: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ExecutionResult(BaseModel):
    """Outcome of executing a trade command."""
    order_id: int
    success: bool
    executed_price: Optional[float] = None
    fees: float = 0.0
    error_message: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class PortfolioSummary(BaseModel):
    """Aggregate view of current trading portfolio."""
    total_exposure: float = 0.0
    open_orders: int = 0
    executed_today: int = 0
    total_invested: float = 0.0
    total_profit: float = 0.0
    orders_by_status: dict[str, int] = Field(default_factory=dict)
