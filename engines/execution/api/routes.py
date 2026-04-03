"""Execution engine API routes."""
import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.database import async_session
from ..application.approval_workflow import ApprovalWorkflow
from ..application.execution_service import ExecutionService
from ..application.risk_engine import RiskEngine
from ..domain.enums import ExecutionMode, OrderType
from ..infrastructure.execution_repository import ExecutionRepository
from ..infrastructure.redis_publisher import ExecutionEventPublisher
from .schemas import (
    ApprovalRequest,
    CompleteTradeRequest,
    CreateOrderRequest,
    ExecutionResultOut,
    OrderOut,
    PortfolioOut,
    RiskAssessmentOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/execution", tags=["execution"])


def _get_service() -> ExecutionService:
    """Factory for ExecutionService with default dependencies."""
    repo = ExecutionRepository(async_session)
    risk_engine = RiskEngine()
    workflow = ApprovalWorkflow()
    publisher = None
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            import redis.asyncio as aioredis
            client = aioredis.from_url(redis_url, decode_responses=True)
            publisher = ExecutionEventPublisher(client)
        except Exception:
            logger.warning("Redis unavailable for execution events")
    return ExecutionService(repo, risk_engine, workflow, publisher)


@router.post("/orders", response_model=OrderOut)
async def create_order(request: CreateOrderRequest):
    """Create a new trade order."""
    service = _get_service()

    try:
        order_type = OrderType(request.order_type)
    except ValueError:
        raise HTTPException(400, f"Invalid order_type: {request.order_type}")

    try:
        mode = ExecutionMode(request.execution_mode)
    except ValueError:
        raise HTTPException(400, f"Invalid execution_mode: {request.execution_mode}")

    order = await service.create_order(
        product_id=request.product_id,
        order_type=order_type,
        marketplace=request.marketplace,
        price=request.price,
        quantity=request.quantity,
        opportunity_id=request.opportunity_id,
        estimated_profit=request.estimated_profit,
        execution_mode=mode,
    )
    return _order_to_out(order)


@router.get("/orders", response_model=list[OrderOut])
async def list_orders(
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
):
    """List trade orders."""
    service = _get_service()
    orders = await service.list_orders(status, limit)
    return [_order_to_out(o) for o in orders]


@router.get("/orders/{order_id}", response_model=OrderOut)
async def get_order(order_id: int):
    """Get order detail."""
    service = _get_service()
    order = await service.get_order(order_id)
    if order is None:
        raise HTTPException(404, f"Order {order_id} not found")
    return _order_to_out(order)


@router.post("/orders/{order_id}/approve", response_model=OrderOut)
async def approve_order(order_id: int, request: ApprovalRequest):
    """Approve or reject a pending order."""
    service = _get_service()
    try:
        if request.approved:
            order = await service.approve_order(order_id, request.approved_by)
        else:
            order = await service.reject_order(
                order_id, request.reason or "", request.approved_by
            )
        return _order_to_out(order)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/orders/{order_id}/execute", response_model=ExecutionResultOut)
async def execute_order(order_id: int):
    """Execute an approved order."""
    service = _get_service()
    try:
        result = await service.execute_order(order_id)
        return ExecutionResultOut(**result.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/orders/{order_id}/cancel", response_model=OrderOut)
async def cancel_order(order_id: int):
    """Cancel a pending or approved order."""
    service = _get_service()
    try:
        order = await service.cancel_order(order_id)
        return _order_to_out(order)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/portfolio", response_model=PortfolioOut)
async def get_portfolio():
    """Get portfolio summary."""
    service = _get_service()
    portfolio = await service.get_portfolio()
    return PortfolioOut(**portfolio.model_dump())


@router.get("/risk-assessment/{product_id}", response_model=RiskAssessmentOut)
async def risk_assessment(
    product_id: int,
    price: float = Query(gt=0),
    quantity: int = Query(1, ge=1),
):
    """Pre-check risk for a potential order."""
    service = _get_service()
    assessment = await service.get_risk_assessment(product_id, price, quantity)
    return RiskAssessmentOut(**assessment.model_dump())


@router.post("/orders/{order_id}/complete")
async def complete_trade(order_id: int, request: CompleteTradeRequest):
    """Report actual trade prices (closes the truth engine feedback loop).

    Call this when a trade completes to record real buy/sell prices.
    The truth engine uses this data to adapt detection thresholds.
    """
    service = _get_service()
    order = await service.get_order(order_id)
    if order is None:
        raise HTTPException(404, f"Order {order_id} not found")

    if not order.opportunity_id:
        raise HTTPException(400, "Order has no linked opportunity")

    try:
        from truth_engine.integration import truth_engine_integration
        await truth_engine_integration.on_order_completed(
            opportunity_id=order.opportunity_id,
            buy_price_actual=request.buy_price_actual,
            sell_price_actual=request.sell_price_actual,
        )
    except Exception as e:
        logger.error("Truth engine complete error: %s", e)
        raise HTTPException(500, f"Failed to record outcome: {e}")

    actual_profit = request.sell_price_actual - request.buy_price_actual
    return {
        "order_id": order_id,
        "opportunity_id": order.opportunity_id,
        "estimated_profit": order.estimated_profit,
        "actual_profit": round(actual_profit, 2),
        "deviation_pct": round(
            ((actual_profit - order.estimated_profit) / order.estimated_profit * 100)
            if order.estimated_profit > 0 else 0.0,
            2,
        ),
        "status": "completed",
    }


def _order_to_out(order) -> OrderOut:
    return OrderOut(
        id=order.id,
        opportunity_id=order.opportunity_id,
        product_id=order.product_id,
        order_type=order.order_type.value if hasattr(order.order_type, "value") else order.order_type,
        marketplace=order.marketplace,
        price=order.price,
        quantity=order.quantity,
        total_cost=order.total_cost,
        estimated_profit=order.estimated_profit,
        status=order.status.value if hasattr(order.status, "value") else order.status,
        approval_state=order.approval_state.value if hasattr(order.approval_state, "value") else order.approval_state,
        execution_mode=order.execution_mode.value if hasattr(order.execution_mode, "value") else order.execution_mode,
        risk_assessment=order.risk_assessment,
        error_message=order.error_message,
        approved_by=order.approved_by,
        approved_at=order.approved_at,
        executed_at=order.executed_at,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )
