"""Repository for execution engine persistence."""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..domain.enums import ApprovalState, ExecutionMode, OrderStatus, OrderType
from ..domain.models import PortfolioSummary, TradeOrder

logger = logging.getLogger(__name__)


class ExecutionRepository:
    """Handles all DB operations for the execution engine."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save_order(self, order: TradeOrder) -> TradeOrder:
        """Insert a new order and return it with generated ID."""
        from api.models import ExecutionOrder

        async with self._session_factory() as session:
            db_order = ExecutionOrder(
                opportunity_id=order.opportunity_id,
                product_id=order.product_id,
                order_type=order.order_type.value,
                marketplace=order.marketplace,
                price=order.price,
                quantity=order.quantity,
                total_cost=order.total_cost,
                estimated_profit=order.estimated_profit,
                status=order.status.value,
                approval_state=order.approval_state.value,
                execution_mode=order.execution_mode.value,
                risk_assessment=json.dumps(order.risk_assessment),
                error_message=order.error_message,
                approved_by=order.approved_by,
                approved_at=order.approved_at,
                executed_at=order.executed_at,
            )
            session.add(db_order)
            await session.commit()
            await session.refresh(db_order)

            order.id = db_order.id
            order.created_at = db_order.created_at
            order.updated_at = db_order.updated_at
            return order

    async def update_order(self, order: TradeOrder) -> None:
        """Update an existing order."""
        async with self._session_factory() as session:
            await session.execute(
                text("""
                    UPDATE execution_orders
                    SET status = :status,
                        approval_state = :approval_state,
                        error_message = :error_message,
                        approved_by = :approved_by,
                        approved_at = :approved_at,
                        executed_at = :executed_at,
                        risk_assessment = :risk_assessment,
                        updated_at = now()
                    WHERE id = :id
                """),
                {
                    "id": order.id,
                    "status": order.status.value,
                    "approval_state": order.approval_state.value,
                    "error_message": order.error_message,
                    "approved_by": order.approved_by,
                    "approved_at": order.approved_at,
                    "executed_at": order.executed_at,
                    "risk_assessment": json.dumps(order.risk_assessment) if isinstance(order.risk_assessment, dict) else order.risk_assessment,
                },
            )
            await session.commit()

    async def get_order(self, order_id: int) -> Optional[TradeOrder]:
        """Fetch a single order by ID."""
        from api.models import ExecutionOrder

        async with self._session_factory() as session:
            stmt = select(ExecutionOrder).where(ExecutionOrder.id == order_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._to_domain(row) if row else None

    async def list_orders(
        self, status: Optional[str] = None, limit: int = 50
    ) -> list[TradeOrder]:
        """List orders with optional status filter."""
        from api.models import ExecutionOrder

        async with self._session_factory() as session:
            stmt = select(ExecutionOrder).order_by(ExecutionOrder.created_at.desc())
            if status:
                stmt = stmt.where(ExecutionOrder.status == status)
            stmt = stmt.limit(limit)

            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [self._to_domain(r) for r in rows]

    async def log_event(
        self,
        order_id: int,
        event_type: str,
        old_status: Optional[str] = None,
        new_status: Optional[str] = None,
        details: Optional[dict] = None,
        actor: str = "system",
    ) -> None:
        """Write to execution_log audit trail."""
        from api.models import ExecutionLog

        async with self._session_factory() as session:
            log_entry = ExecutionLog(
                order_id=order_id,
                event_type=event_type,
                old_status=old_status,
                new_status=new_status,
                details=json.dumps(details) if details else None,
                actor=actor,
            )
            session.add(log_entry)
            await session.commit()

    async def get_portfolio_summary(self) -> PortfolioSummary:
        """Compute aggregate portfolio state."""
        async with self._session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT
                        COALESCE(SUM(CASE WHEN status IN ('draft','pending_approval','approved','executing')
                                     THEN total_cost ELSE 0 END), 0) AS total_exposure,
                        COUNT(*) FILTER (WHERE status IN ('draft','pending_approval','approved','executing'))
                            AS open_orders,
                        COUNT(*) FILTER (WHERE status = 'executed' AND executed_at >= CURRENT_DATE)
                            AS executed_today,
                        COALESCE(SUM(CASE WHEN status = 'executed' THEN total_cost ELSE 0 END), 0)
                            AS total_invested,
                        COALESCE(SUM(CASE WHEN status = 'executed' THEN estimated_profit ELSE 0 END), 0)
                            AS total_profit
                    FROM execution_orders
                """)
            )
            row = result.fetchone()

            # Status breakdown
            status_result = await session.execute(
                text("SELECT status, COUNT(*) FROM execution_orders GROUP BY status")
            )
            status_counts = {r[0]: r[1] for r in status_result.fetchall()}

            return PortfolioSummary(
                total_exposure=float(row[0]) if row else 0,
                open_orders=row[1] if row else 0,
                executed_today=row[2] if row else 0,
                total_invested=float(row[3]) if row else 0,
                total_profit=float(row[4]) if row else 0,
                orders_by_status=status_counts,
            )

    @staticmethod
    def _to_domain(row) -> TradeOrder:
        risk = row.risk_assessment
        if isinstance(risk, str):
            try:
                risk = json.loads(risk)
            except (json.JSONDecodeError, TypeError):
                risk = {}

        return TradeOrder(
            id=row.id,
            opportunity_id=row.opportunity_id,
            product_id=row.product_id,
            order_type=OrderType(row.order_type),
            marketplace=row.marketplace,
            price=float(row.price),
            quantity=row.quantity,
            total_cost=float(row.total_cost),
            estimated_profit=float(row.estimated_profit),
            status=OrderStatus(row.status),
            approval_state=ApprovalState(row.approval_state),
            execution_mode=ExecutionMode(row.execution_mode),
            risk_assessment=risk or {},
            error_message=row.error_message,
            approved_by=row.approved_by,
            approved_at=row.approved_at,
            executed_at=row.executed_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
