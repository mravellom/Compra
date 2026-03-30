"""
DB-backed Portfolio Store — crash-safe capital management.

Replaces in-memory singleton with PostgreSQL-backed state.
Uses advisory locks (pg_advisory_xact_lock) for atomic capital mutations.
All state changes are persisted within a single transaction.

Usage:
    store = PortfolioDBStore(async_session)
    async with store.locked_portfolio() as portfolio:
        # portfolio is loaded + locked (FOR UPDATE)
        tracker.open_position(portfolio, position)
        # on exit: auto-persists to DB + commits
"""
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import PortfolioState, Position, PositionStatus

logger = logging.getLogger(__name__)

# Advisory lock ID for portfolio mutations (arbitrary constant)
_PORTFOLIO_LOCK_ID = 900_001


class PortfolioDBStore:
    """Database-backed portfolio state with advisory locking."""

    def __init__(self, session_factory):
        self._session_factory = session_factory

    @asynccontextmanager
    async def locked_portfolio(self):
        """Load portfolio with advisory lock, yield for mutation, persist on exit.

        Usage:
            async with store.locked_portfolio() as portfolio:
                tracker.open_position(portfolio, position)
            # auto-persisted + committed here
        """
        async with self._session_factory() as session:
            async with session.begin():
                # Acquire advisory lock (released on commit/rollback)
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_id)"),
                    {"lock_id": _PORTFOLIO_LOCK_ID},
                )

                # Load portfolio state (FOR UPDATE)
                portfolio = await self._load_state(session)

                yield portfolio

                # Persist mutations back to DB
                await self._save_state(session, portfolio)

    async def read_portfolio(self) -> PortfolioState:
        """Read-only snapshot (no lock). For display/metrics only."""
        async with self._session_factory() as session:
            return await self._load_state(session, for_update=False)

    async def _load_state(self, session: AsyncSession, for_update: bool = True) -> PortfolioState:
        """Load portfolio + positions from DB."""
        query = "SELECT * FROM portfolio_state WHERE id = 1"
        if for_update:
            query += " FOR UPDATE"
        row = (await session.execute(text(query))).mappings().first()

        if not row:
            # First run: create initial state
            await session.execute(text("""
                INSERT INTO portfolio_state (id, strategy_mode, total_capital, available_capital, peak_capital)
                VALUES (1, 'conservative', 5000.00, 5000.00, 5000.00)
                ON CONFLICT (id) DO NOTHING
            """))
            requery = "SELECT * FROM portfolio_state WHERE id = 1"
            if for_update:
                requery += " FOR UPDATE"
            row = (await session.execute(text(requery))).mappings().first()

        # Load open positions
        pos_rows = (await session.execute(
            text("SELECT * FROM portfolio_positions WHERE portfolio_id = 1 AND status = 'open' ORDER BY opened_at")
        )).mappings().all()

        positions = [
            Position(
                opportunity_id=p["opportunity_id"] or 0,
                allocated_amount=float(p["allocated_amount"]),
                entry_price=float(p["entry_price"]),
                expected_profit=float(p["expected_profit"]),
                expected_roi=float(p["expected_roi"]),
                risk_score=float(p["risk_score"]),
                status=PositionStatus(p["status"]),
                product_id=p["product_id"],
                marketplace=p["marketplace"] or "",
                category=p["category"] or "",
                timestamp=p["opened_at"],
            )
            for p in pos_rows
        ]

        return PortfolioState(
            total_capital=float(row["total_capital"]),
            available_capital=float(row["available_capital"]),
            allocated_capital=float(row["allocated_capital"]),
            reserved_capital=float(row["reserved_capital"]),
            realized_profit=float(row["realized_profit"]),
            unrealized_profit=float(row["unrealized_profit"]),
            peak_capital=float(row["peak_capital"]),
            drawdown_pct=float(row["drawdown_pct"]),
            max_drawdown_pct=float(row["max_drawdown_pct"]),
            consecutive_losses=int(row["consecutive_losses"]),
            total_trades=int(row["total_trades"]),
            winning_trades=int(row["winning_trades"]),
            losing_trades=int(row["losing_trades"]),
            daily_loss_usd=float(row["daily_loss_usd"]),
            daily_loss_reset_date=str(row["daily_loss_reset_date"]) if row["daily_loss_reset_date"] else None,
            last_loss_at=row["last_loss_at"],
            active_positions=positions,
        )

    async def _save_state(self, session: AsyncSession, portfolio: PortfolioState) -> None:
        """Persist portfolio state + positions to DB."""
        await session.execute(
            text("""
                UPDATE portfolio_state SET
                    total_capital = :total_capital,
                    available_capital = :available_capital,
                    allocated_capital = :allocated_capital,
                    reserved_capital = :reserved_capital,
                    realized_profit = :realized_profit,
                    unrealized_profit = :unrealized_profit,
                    peak_capital = :peak_capital,
                    drawdown_pct = :drawdown_pct,
                    max_drawdown_pct = :max_drawdown_pct,
                    consecutive_losses = :consecutive_losses,
                    total_trades = :total_trades,
                    winning_trades = :winning_trades,
                    losing_trades = :losing_trades,
                    daily_loss_usd = :daily_loss_usd,
                    daily_loss_reset_date = :daily_loss_reset_date,
                    last_loss_at = :last_loss_at,
                    updated_at = NOW()
                WHERE id = 1
            """),
            {
                "total_capital": portfolio.total_capital,
                "available_capital": portfolio.available_capital,
                "allocated_capital": portfolio.allocated_capital,
                "reserved_capital": portfolio.reserved_capital,
                "realized_profit": portfolio.realized_profit,
                "unrealized_profit": portfolio.unrealized_profit,
                "peak_capital": portfolio.peak_capital,
                "drawdown_pct": portfolio.drawdown_pct,
                "max_drawdown_pct": portfolio.max_drawdown_pct,
                "consecutive_losses": portfolio.consecutive_losses,
                "total_trades": portfolio.total_trades,
                "winning_trades": portfolio.winning_trades,
                "losing_trades": portfolio.losing_trades,
                "daily_loss_usd": portfolio.daily_loss_usd,
                "daily_loss_reset_date": portfolio.daily_loss_reset_date,
                "last_loss_at": portfolio.last_loss_at,
            },
        )

        # Sync positions: insert new OPEN, update CLOSED/FAILED
        for pos in portfolio.active_positions:
            if pos.status == PositionStatus.OPEN:
                await session.execute(
                    text("""
                        INSERT INTO portfolio_positions
                            (portfolio_id, opportunity_id, product_id, allocated_amount,
                             entry_price, expected_profit, expected_roi, risk_score,
                             status, marketplace, category)
                        VALUES (1, :opp_id, :prod_id, :alloc, :entry, :exp_profit,
                                :exp_roi, :risk, 'open', :mp, :cat)
                        ON CONFLICT (opportunity_id) WHERE status = 'open' DO NOTHING
                    """),
                    {
                        "opp_id": pos.opportunity_id,
                        "prod_id": pos.product_id,
                        "alloc": pos.allocated_amount,
                        "entry": pos.entry_price,
                        "exp_profit": pos.expected_profit,
                        "exp_roi": pos.expected_roi,
                        "risk": pos.risk_score,
                        "mp": pos.marketplace,
                        "cat": pos.category,
                    },
                )
            elif pos.status in (PositionStatus.CLOSED, PositionStatus.FAILED):
                await session.execute(
                    text("""
                        UPDATE portfolio_positions
                        SET status = :status,
                            exit_price = :exit_price,
                            actual_profit = :actual_profit,
                            closed_at = NOW()
                        WHERE opportunity_id = :opp_id AND status = 'open'
                    """),
                    {
                        "status": pos.status.value,
                        "exit_price": pos.exit_price,
                        "actual_profit": pos.actual_profit,
                        "opp_id": pos.opportunity_id,
                    },
                )

        logger.info(
            "Portfolio persisted: total=$%.2f available=$%.2f allocated=$%.2f trades=%d",
            portfolio.total_capital, portfolio.available_capital,
            portfolio.allocated_capital, portfolio.total_trades,
        )
