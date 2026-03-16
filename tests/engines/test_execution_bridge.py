"""
Tests for Execution Bridge — Orchestrator → Execution pipeline connection.

Tests:
  - Risk gate enforcement (failed risk blocks order)
  - Auto-approval uses assessment.passed
  - DuplicateGuard rejects duplicate opportunity_ids
  - ExecutionBridge event handling
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from engines.execution.application.approval_workflow import ApprovalWorkflow
from engines.execution.application.execution_bridge import ExecutionBridge
from engines.execution.application.execution_service import ExecutionService
from engines.execution.application.risk_engine import RiskEngine
from engines.execution.domain.enums import (
    ApprovalState,
    ExecutionMode,
    OrderStatus,
    OrderType,
)
from engines.execution.domain.models import PortfolioSummary, RiskAssessment, TradeOrder
from engines.execution.domain.risk_guards import DuplicateGuard


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def mock_repo():
    repo = AsyncMock()
    repo.save_order = AsyncMock(side_effect=lambda o: _with_id(o, 1))
    repo.update_order = AsyncMock()
    repo.get_order = AsyncMock()
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


# ═══════════════════════════════════════════════════════════════
# 1. Risk Gate Enforcement
# ═══════════════════════════════════════════════════════════════


class TestRiskGateEnforcement:

    @pytest.mark.asyncio
    async def test_failed_risk_blocks_order(self, mock_repo, mock_publisher, monkeypatch):
        """Order with failed risk assessment should be CANCELLED, not PENDING."""
        monkeypatch.setenv("MAX_TOTAL_EXPOSURE", "10")  # Very low limit
        monkeypatch.setenv("MAX_SINGLE_ORDER", "10000")
        monkeypatch.setenv("MAX_DAILY_ORDERS", "100")
        monkeypatch.setenv("EXECUTION_MIN_ROI", "0.01")
        monkeypatch.setenv("EXECUTION_MIN_PROFIT", "1.0")

        # Portfolio already has $8 exposure
        mock_repo.get_portfolio_summary.return_value = PortfolioSummary(
            total_exposure=8.0,
        )

        service = ExecutionService(
            mock_repo, RiskEngine(), ApprovalWorkflow(), mock_publisher,
        )

        # This $50 order exceeds the $10 exposure limit
        order = await service.create_order(
            product_id=1,
            order_type=OrderType.BUY,
            marketplace="amazon_us",
            price=50.0,
            estimated_profit=10.0,
        )

        assert order.status == OrderStatus.CANCELLED
        assert order.approval_state == ApprovalState.REJECTED
        assert "Risk check failed" in (order.error_message or "")

    @pytest.mark.asyncio
    async def test_passed_risk_continues_normally(self, service, mock_repo):
        """Order that passes all guards should proceed to PENDING_APPROVAL."""
        order = await service.create_order(
            product_id=1,
            order_type=OrderType.BUY,
            marketplace="amazon_us",
            price=100.0,
            estimated_profit=20.0,
        )

        assert order.status == OrderStatus.PENDING_APPROVAL

    @pytest.mark.asyncio
    async def test_risk_gate_logs_event(self, mock_repo, mock_publisher, monkeypatch):
        """Blocked orders should log a risk_rejected event."""
        monkeypatch.setenv("EXECUTION_MIN_PROFIT", "999")  # Impossible threshold
        monkeypatch.setenv("MAX_TOTAL_EXPOSURE", "100000")
        monkeypatch.setenv("MAX_SINGLE_ORDER", "10000")
        monkeypatch.setenv("MAX_DAILY_ORDERS", "100")
        monkeypatch.setenv("EXECUTION_MIN_ROI", "0.01")

        service = ExecutionService(
            mock_repo, RiskEngine(), ApprovalWorkflow(), mock_publisher,
        )

        await service.create_order(
            product_id=1,
            order_type=OrderType.BUY,
            marketplace="amazon_us",
            price=100.0,
            estimated_profit=5.0,  # Below $999 threshold
        )

        # Should have logged risk_rejected event
        log_calls = mock_repo.log_event.call_args_list
        assert any(
            call.args[1] == "risk_rejected" for call in log_calls
        ), f"Expected risk_rejected event, got: {[c.args[1] for c in log_calls]}"


# ═══════════════════════════════════════════════════════════════
# 2. Auto-Approval Fix
# ═══════════════════════════════════════════════════════════════


class TestAutoApprovalFix:

    @pytest.mark.asyncio
    async def test_auto_approve_with_passed_risk(self, mock_repo, mock_publisher, monkeypatch):
        """AUTO mode + passed risk + low cost → auto-approved."""
        monkeypatch.setenv("AUTO_APPROVE_MAX_COST", "200")
        monkeypatch.setenv("MAX_TOTAL_EXPOSURE", "100000")
        monkeypatch.setenv("MAX_SINGLE_ORDER", "10000")
        monkeypatch.setenv("MAX_DAILY_ORDERS", "100")
        monkeypatch.setenv("EXECUTION_MIN_ROI", "0.01")
        monkeypatch.setenv("EXECUTION_MIN_PROFIT", "1.0")

        service = ExecutionService(
            mock_repo, RiskEngine(), ApprovalWorkflow(), mock_publisher,
        )

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
    async def test_auto_above_cost_needs_approval(self, mock_repo, mock_publisher, monkeypatch):
        """AUTO mode but above cost threshold → needs manual approval."""
        monkeypatch.setenv("AUTO_APPROVE_MAX_COST", "10")
        monkeypatch.setenv("MAX_TOTAL_EXPOSURE", "100000")
        monkeypatch.setenv("MAX_SINGLE_ORDER", "10000")
        monkeypatch.setenv("MAX_DAILY_ORDERS", "100")
        monkeypatch.setenv("EXECUTION_MIN_ROI", "0.01")
        monkeypatch.setenv("EXECUTION_MIN_PROFIT", "1.0")

        service = ExecutionService(
            mock_repo, RiskEngine(), ApprovalWorkflow(), mock_publisher,
        )

        order = await service.create_order(
            product_id=1,
            order_type=OrderType.BUY,
            marketplace="amazon_us",
            price=100.0,
            estimated_profit=20.0,
            execution_mode=ExecutionMode.AUTO,
        )

        assert order.status == OrderStatus.PENDING_APPROVAL


# ═══════════════════════════════════════════════════════════════
# 3. DuplicateGuard
# ═══════════════════════════════════════════════════════════════


class TestDuplicateGuard:

    def test_no_opportunity_id_passes(self):
        guard = DuplicateGuard()
        order = TradeOrder(
            product_id=1, order_type=OrderType.BUY,
            marketplace="test", price=10, total_cost=10,
            opportunity_id=None,
        )
        portfolio = PortfolioSummary()
        passed, _ = guard.evaluate(order, portfolio)
        assert passed is True

    def test_new_opportunity_passes(self):
        guard = DuplicateGuard()
        order = TradeOrder(
            product_id=1, order_type=OrderType.BUY,
            marketplace="test", price=10, total_cost=10,
            opportunity_id=42,
        )
        portfolio = PortfolioSummary(
            active_opportunity_ids=[10, 20, 30],
        )
        passed, _ = guard.evaluate(order, portfolio)
        assert passed is True

    def test_duplicate_opportunity_rejected(self):
        guard = DuplicateGuard()
        order = TradeOrder(
            product_id=1, order_type=OrderType.BUY,
            marketplace="test", price=10, total_cost=10,
            opportunity_id=42,
        )
        portfolio = PortfolioSummary(
            active_opportunity_ids=[42, 50],
        )
        passed, reason = guard.evaluate(order, portfolio)
        assert passed is False
        assert "42" in reason

    @pytest.mark.asyncio
    async def test_duplicate_blocks_order_creation(self, mock_repo, mock_publisher, monkeypatch):
        """Full integration: duplicate opportunity_id should block order."""
        monkeypatch.setenv("MAX_TOTAL_EXPOSURE", "100000")
        monkeypatch.setenv("MAX_SINGLE_ORDER", "10000")
        monkeypatch.setenv("MAX_DAILY_ORDERS", "100")
        monkeypatch.setenv("EXECUTION_MIN_ROI", "0.01")
        monkeypatch.setenv("EXECUTION_MIN_PROFIT", "1.0")

        # Portfolio has active order for opportunity 42
        mock_repo.get_portfolio_summary.return_value = PortfolioSummary(
            active_opportunity_ids=[42],
        )

        service = ExecutionService(
            mock_repo, RiskEngine(), ApprovalWorkflow(), mock_publisher,
        )

        order = await service.create_order(
            product_id=1,
            order_type=OrderType.BUY,
            marketplace="amazon_us",
            price=100.0,
            estimated_profit=20.0,
            opportunity_id=42,  # Duplicate!
        )

        assert order.status == OrderStatus.CANCELLED
        assert "already exists" in (order.error_message or "").lower()


# ═══════════════════════════════════════════════════════════════
# 4. ExecutionBridge
# ═══════════════════════════════════════════════════════════════


class TestExecutionBridge:

    @pytest.mark.asyncio
    async def test_handle_execution_recommended(self, service):
        bridge = ExecutionBridge(
            redis_client=AsyncMock(),
            execution_service=service,
        )

        data = {
            "opportunity_id": 1,
            "product_id": 100,
            "score": 85.0,
            "signal_strength": "strong",
            "buy_marketplace": "amazon_us",
            "buy_price": 50.0,
            "net_profit": 25.0,
        }

        order = await bridge._handle_execution_recommended(data)
        assert order is not None
        assert order.product_id == 100
        assert order.opportunity_id == 1
        assert order.execution_mode == ExecutionMode.AUTO  # strong + score>=80

    @pytest.mark.asyncio
    async def test_skip_low_score(self, service, monkeypatch):
        monkeypatch.setenv("BRIDGE_MIN_SCORE", "60")
        bridge = ExecutionBridge(
            redis_client=AsyncMock(),
            execution_service=service,
        )

        data = {
            "opportunity_id": 1,
            "product_id": 100,
            "score": 40.0,  # Below threshold
            "signal_strength": "strong",
            "buy_marketplace": "amazon_us",
            "buy_price": 50.0,
            "net_profit": 25.0,
        }

        order = await bridge._handle_execution_recommended(data)
        assert order is None

    @pytest.mark.asyncio
    async def test_skip_weak_signal(self, service, monkeypatch):
        monkeypatch.setenv("BRIDGE_MIN_SIGNAL", "moderate")
        bridge = ExecutionBridge(
            redis_client=AsyncMock(),
            execution_service=service,
        )

        data = {
            "opportunity_id": 1,
            "product_id": 100,
            "score": 80.0,
            "signal_strength": "weak",  # Below threshold
            "buy_marketplace": "amazon_us",
            "buy_price": 50.0,
            "net_profit": 25.0,
        }

        order = await bridge._handle_execution_recommended(data)
        assert order is None

    @pytest.mark.asyncio
    async def test_assisted_mode_for_moderate_signal(self, service):
        bridge = ExecutionBridge(
            redis_client=AsyncMock(),
            execution_service=service,
        )

        data = {
            "opportunity_id": 2,
            "product_id": 200,
            "score": 70.0,
            "signal_strength": "moderate",
            "buy_marketplace": "ebay",
            "buy_price": 80.0,
            "net_profit": 15.0,
        }

        order = await bridge._handle_execution_recommended(data)
        assert order is not None
        assert order.execution_mode == ExecutionMode.ASSISTED

    @pytest.mark.asyncio
    async def test_no_price_skips(self, service):
        bridge = ExecutionBridge(
            redis_client=AsyncMock(),
            execution_service=service,
        )

        data = {
            "opportunity_id": 1,
            "product_id": 100,
            "score": 90.0,
            "signal_strength": "strong",
            "buy_marketplace": "amazon_us",
            # No price!
            "net_profit": 25.0,
        }

        order = await bridge._handle_execution_recommended(data)
        assert order is None

    @pytest.mark.asyncio
    async def test_stats_tracking(self, service):
        bridge = ExecutionBridge(
            redis_client=AsyncMock(),
            execution_service=service,
        )

        assert bridge.stats["consumed"] == 0
        assert bridge.stats["orders_created"] == 0

    @pytest.mark.asyncio
    async def test_process_non_execution_event(self, service):
        """Non execution_recommended events should be acked and skipped."""
        mock_redis = AsyncMock()
        bridge = ExecutionBridge(redis_client=mock_redis, execution_service=service)

        await bridge._process_message("msg-1", {
            "event_type": "pipeline_completed",
            "data": "{}",
        })

        mock_redis.xack.assert_called_once()
        assert bridge.stats["consumed"] == 0
