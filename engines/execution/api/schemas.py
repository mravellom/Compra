from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CreateOrderRequest(BaseModel):
    product_id: int
    order_type: str = Field(description="buy, sell, or list_item")
    marketplace: str
    price: float = Field(gt=0)
    quantity: int = Field(1, ge=1)
    opportunity_id: Optional[int] = None
    estimated_profit: float = 0
    execution_mode: str = Field("manual", description="manual, assisted, or auto")


class ApprovalRequest(BaseModel):
    approved: bool
    reason: Optional[str] = None
    approved_by: str = "user"


class OrderOut(BaseModel):
    id: Optional[int] = None
    opportunity_id: Optional[int] = None
    product_id: int
    order_type: str
    marketplace: str
    price: float
    quantity: int = 1
    total_cost: float = 0
    estimated_profit: float = 0
    status: str = "draft"
    approval_state: str = "pending"
    execution_mode: str = "manual"
    risk_assessment: dict = Field(default_factory=dict)
    error_message: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ExecutionResultOut(BaseModel):
    order_id: int
    success: bool
    executed_price: Optional[float] = None
    fees: float = 0
    error_message: Optional[str] = None


class PortfolioOut(BaseModel):
    total_exposure: float = 0
    open_orders: int = 0
    executed_today: int = 0
    total_invested: float = 0
    total_profit: float = 0
    orders_by_status: dict[str, int] = Field(default_factory=dict)


class RiskAssessmentOut(BaseModel):
    passed: bool = False
    total_exposure: float = 0
    daily_trade_count: int = 0
    guards_passed: list[str] = Field(default_factory=list)
    guards_failed: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class CompleteTradeRequest(BaseModel):
    """Report actual trade outcome (closes the feedback loop)."""
    buy_price_actual: float = Field(gt=0)
    sell_price_actual: float = Field(gt=0)
