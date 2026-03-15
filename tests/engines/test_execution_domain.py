"""Tests for the execution engine domain layer."""
import pytest

from engines.execution.domain.enums import (
    ApprovalState,
    ExecutionMode,
    OrderStatus,
    OrderType,
    VALID_TRANSITIONS,
)
from engines.execution.domain.models import (
    ExecutionResult,
    PortfolioSummary,
    RiskAssessment,
    TradeOrder,
)
from engines.execution.domain.commands import (
    BuyCommand,
    CancelCommand,
    SellCommand,
    create_command,
)


# ─── Enum tests ─────────────────────────────────────────────────────

class TestEnums:
    def test_order_status_values(self):
        assert OrderStatus.DRAFT.value == "draft"
        assert OrderStatus.EXECUTED.value == "executed"

    def test_valid_transitions_from_draft(self):
        assert OrderStatus.PENDING_APPROVAL in VALID_TRANSITIONS[OrderStatus.DRAFT]
        assert OrderStatus.CANCELLED in VALID_TRANSITIONS[OrderStatus.DRAFT]
        assert OrderStatus.EXECUTED not in VALID_TRANSITIONS[OrderStatus.DRAFT]

    def test_no_transitions_from_executed(self):
        assert len(VALID_TRANSITIONS[OrderStatus.EXECUTED]) == 0

    def test_failed_can_retry(self):
        assert OrderStatus.DRAFT in VALID_TRANSITIONS[OrderStatus.FAILED]


# ─── TradeOrder tests ───────────────────────────────────────────────

class TestTradeOrder:
    def test_create_order(self):
        order = TradeOrder(
            product_id=1,
            order_type=OrderType.BUY,
            marketplace="amazon_us",
            price=99.99,
            quantity=2,
            total_cost=199.98,
        )
        assert order.status == OrderStatus.DRAFT
        assert order.approval_state == ApprovalState.PENDING
        assert order.execution_mode == ExecutionMode.MANUAL

    def test_risk_assessment_model(self):
        ra = RiskAssessment(
            passed=True,
            total_exposure=500,
            guards_passed=["max_exposure", "daily_limit"],
        )
        assert ra.passed
        assert len(ra.guards_passed) == 2

    def test_portfolio_summary(self):
        ps = PortfolioSummary(
            total_exposure=5000,
            open_orders=10,
            executed_today=5,
        )
        assert ps.total_exposure == 5000


# ─── Command Pattern tests ──────────────────────────────────────────

def _make_order(**kwargs) -> TradeOrder:
    defaults = {
        "id": 1,
        "product_id": 100,
        "order_type": OrderType.BUY,
        "marketplace": "amazon_us",
        "price": 50.0,
        "quantity": 1,
        "total_cost": 50.0,
    }
    defaults.update(kwargs)
    return TradeOrder(**defaults)


class TestBuyCommand:
    def test_execute_success(self):
        order = _make_order()
        cmd = BuyCommand(order)
        result = cmd.execute()
        assert result.success
        assert result.executed_price == 50.0
        assert result.order_id == 1

    def test_validate_negative_price(self):
        order = _make_order(price=-10)
        cmd = BuyCommand(order)
        valid, reason = cmd.validate()
        assert not valid
        assert "positive" in reason.lower()

    def test_validate_zero_quantity(self):
        order = _make_order(quantity=0)
        cmd = BuyCommand(order)
        valid, reason = cmd.validate()
        assert not valid

    def test_execute_with_invalid_data(self):
        order = _make_order(price=-5)
        cmd = BuyCommand(order)
        result = cmd.execute()
        assert not result.success
        assert result.error_message is not None


class TestSellCommand:
    def test_execute_success(self):
        order = _make_order(order_type=OrderType.SELL)
        cmd = SellCommand(order)
        result = cmd.execute()
        assert result.success

    def test_validate_ok(self):
        order = _make_order(order_type=OrderType.SELL, price=75)
        cmd = SellCommand(order)
        valid, _ = cmd.validate()
        assert valid


class TestCancelCommand:
    def test_cancel_draft(self):
        order = _make_order(status=OrderStatus.DRAFT)
        cmd = CancelCommand(order)
        result = cmd.execute()
        assert result.success

    def test_cancel_executed_fails(self):
        order = _make_order(status=OrderStatus.EXECUTED)
        cmd = CancelCommand(order)
        result = cmd.execute()
        assert not result.success

    def test_cancel_cancelled_fails(self):
        order = _make_order(status=OrderStatus.CANCELLED)
        cmd = CancelCommand(order)
        result = cmd.execute()
        assert not result.success


class TestCommandFactory:
    def test_create_buy_command(self):
        order = _make_order(order_type=OrderType.BUY)
        cmd = create_command(order)
        assert isinstance(cmd, BuyCommand)

    def test_create_sell_command(self):
        order = _make_order(order_type=OrderType.SELL)
        cmd = create_command(order)
        assert isinstance(cmd, SellCommand)

    def test_create_list_item_command(self):
        order = _make_order(order_type=OrderType.LIST_ITEM)
        cmd = create_command(order)
        assert isinstance(cmd, SellCommand)  # list_item uses sell logic
