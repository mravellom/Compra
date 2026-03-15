"""Tests for the execution service layer."""
import pytest
from unittest.mock import AsyncMock

from engines.execution.application.approval_workflow import ApprovalWorkflow
from engines.execution.application.execution_service import ExecutionService
from engines.execution.application.risk_engine import RiskEngine
from engines.execution.domain.enums import (
    ApprovalState,
    ExecutionMode,
    OrderStatus,
    OrderType,
)
from engines.execution.domain.models import PortfolioSummary, RiskAssessment, TradeOrder


@pytest.fixture
def mock_repo():
    repo = AsyncMock()
    repo.save_order = AsyncMock(side_effect=lambda o: _with_id(o, 1))
    repo.update_order = AsyncMock()
    repo.get_order = AsyncMock()
    repo.list_orders = AsyncMock(return_value=[])
    repo.log_event = AsyncMock()
    repo.get_portfolio_summary = AsyncMock(return_value=PortfolioSummary())
    return repo


@pytest.fixture
def mock_publisher():
    pub = AsyncMock()
    pub.publish = AsyncMock()
    return pub


@pytest.fixture
def service(mock_repo, mock_publisher, monkeypatch):
    monkeypatch.setenv("MAX_TOTAL_EXPOSURE", "100000")
    monkeypatch.setenv("MAX_SINGLE_ORDER", "10000")
    monkeypatch.setenv("MAX_DAILY_ORDERS", "100")
    monkeypatch.setenv("EXECUTION_MIN_ROI", "0.01")
    monkeypatch.setenv("EXECUTION_MIN_PROFIT", "1.0")

    risk_engine = RiskEngine()
    workflow = ApprovalWorkflow()
    return ExecutionService(mock_repo, risk_engine, workflow, mock_publisher)


def _with_id(order: TradeOrder, order_id: int) -> TradeOrder:
    order.id = order_id
    return order


# ─── Order creation ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_order_manual(service, mock_repo):
    """Manual orders should go to pending_approval."""
    order = await service.create_order(
        product_id=1,
        order_type=OrderType.BUY,
        marketplace="amazon_us",
        price=100.0,
        estimated_profit=20.0,
    )

    assert order.id == 1
    assert order.status == OrderStatus.PENDING_APPROVAL
    mock_repo.save_order.assert_called_once()
    mock_repo.log_event.assert_called_once()


@pytest.mark.asyncio
async def test_create_order_auto_below_threshold(service, mock_repo, monkeypatch):
    """Auto orders below threshold should be auto-approved."""
    monkeypatch.setenv("AUTO_APPROVE_MAX_COST", "200")
    monkeypatch.setenv("AUTO_APPROVE_MIN_CONFIDENCE", "0")

    order = await service.create_order(
        product_id=1,
        order_type=OrderType.BUY,
        marketplace="amazon_us",
        price=30.0,
        estimated_profit=10.0,
        execution_mode=ExecutionMode.AUTO,
    )

    assert order.status == OrderStatus.APPROVED
    assert order.approval_state == ApprovalState.AUTO_APPROVED


@pytest.mark.asyncio
async def test_create_order_auto_above_threshold(service, mock_repo, monkeypatch):
    """Auto orders above threshold should still need approval."""
    monkeypatch.setenv("AUTO_APPROVE_MAX_COST", "10")

    order = await service.create_order(
        product_id=1,
        order_type=OrderType.BUY,
        marketplace="amazon_us",
        price=100.0,
        estimated_profit=20.0,
        execution_mode=ExecutionMode.AUTO,
    )

    assert order.status == OrderStatus.PENDING_APPROVAL


# ─── Approval workflow ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_approve_order(service, mock_repo):
    """Should transition from pending to approved."""
    pending_order = TradeOrder(
        id=1, product_id=1, order_type=OrderType.BUY,
        marketplace="amazon_us", price=100, total_cost=100,
        status=OrderStatus.PENDING_APPROVAL,
        approval_state=ApprovalState.PENDING,
    )
    mock_repo.get_order.return_value = pending_order

    result = await service.approve_order(1, "admin")

    assert result.status == OrderStatus.APPROVED
    assert result.approved_by == "admin"
    mock_repo.update_order.assert_called_once()


@pytest.mark.asyncio
async def test_reject_order(service, mock_repo):
    """Should transition to cancelled with rejection reason."""
    pending_order = TradeOrder(
        id=1, product_id=1, order_type=OrderType.BUY,
        marketplace="amazon_us", price=100, total_cost=100,
        status=OrderStatus.PENDING_APPROVAL,
        approval_state=ApprovalState.PENDING,
    )
    mock_repo.get_order.return_value = pending_order

    result = await service.reject_order(1, "Too risky")

    assert result.status == OrderStatus.CANCELLED
    assert result.approval_state == ApprovalState.REJECTED


# ─── Execution ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_approved_order(service, mock_repo, mock_publisher):
    """Should execute an approved order successfully."""
    approved_order = TradeOrder(
        id=1, product_id=1, order_type=OrderType.BUY,
        marketplace="amazon_us", price=100, quantity=1, total_cost=100,
        status=OrderStatus.APPROVED,
    )
    mock_repo.get_order.return_value = approved_order

    result = await service.execute_order(1)

    assert result.success
    assert result.order_id == 1
    mock_publisher.publish.assert_called()


@pytest.mark.asyncio
async def test_execute_non_approved_fails(service, mock_repo):
    """Should reject execution of non-approved orders."""
    draft_order = TradeOrder(
        id=1, product_id=1, order_type=OrderType.BUY,
        marketplace="amazon_us", price=100, total_cost=100,
        status=OrderStatus.DRAFT,
    )
    mock_repo.get_order.return_value = draft_order

    with pytest.raises(ValueError, match="must be approved"):
        await service.execute_order(1)


@pytest.mark.asyncio
async def test_execute_not_found(service, mock_repo):
    """Should raise for non-existent order."""
    mock_repo.get_order.return_value = None

    with pytest.raises(ValueError, match="not found"):
        await service.execute_order(999)


# ─── Cancellation ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_pending_order(service, mock_repo):
    """Should cancel a pending order."""
    pending_order = TradeOrder(
        id=1, product_id=1, order_type=OrderType.BUY,
        marketplace="amazon_us", price=100, total_cost=100,
        status=OrderStatus.PENDING_APPROVAL,
    )
    mock_repo.get_order.return_value = pending_order

    result = await service.cancel_order(1)
    assert result.status == OrderStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_executed_order_fails(service, mock_repo):
    """Cannot cancel an already executed order."""
    executed_order = TradeOrder(
        id=1, product_id=1, order_type=OrderType.BUY,
        marketplace="amazon_us", price=100, total_cost=100,
        status=OrderStatus.EXECUTED,
    )
    mock_repo.get_order.return_value = executed_order

    with pytest.raises(ValueError):
        await service.cancel_order(1)


# ─── Portfolio & risk ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_portfolio(service, mock_repo):
    """Should return portfolio summary."""
    portfolio = await service.get_portfolio()
    assert isinstance(portfolio, PortfolioSummary)


@pytest.mark.asyncio
async def test_risk_assessment_precheck(service, mock_repo):
    """Should evaluate risk without creating an order."""
    assessment = await service.get_risk_assessment(1, 100.0, 1)
    assert isinstance(assessment, RiskAssessment)
    # Assessment may fail min_roi/min_profit guards since estimated_profit=0
    # but it should still return a valid assessment object
    assert len(assessment.guards_passed) + len(assessment.guards_failed) == 6
