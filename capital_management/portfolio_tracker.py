"""
Portfolio Tracker — evolves capital state after each trade event.

Handles:
- Opening positions (capital allocation)
- Closing positions on success (realize profit)
- Closing positions on failure (realize loss)
- Drawdown tracking and high-water mark updates
- Daily loss tracking
"""
import logging
from datetime import datetime, timezone

from .models import PortfolioState, Position, PositionStatus

logger = logging.getLogger(__name__)


class PortfolioTracker:
    """Mutates PortfolioState based on trade lifecycle events."""

    def open_position(
        self,
        portfolio: PortfolioState,
        position: Position,
    ) -> PortfolioState:
        """Allocate capital to a new position.

        Moves capital from available → allocated.
        """
        if position.allocated_amount > portfolio.available_capital:
            logger.error(
                "Cannot open position: need $%.2f, have $%.2f",
                position.allocated_amount, portfolio.available_capital,
            )
            raise ValueError(
                f"Insufficient capital: need ${position.allocated_amount:.2f}, "
                f"available ${portfolio.available_capital:.2f}"
            )

        position.status = PositionStatus.OPEN
        portfolio.available_capital -= position.allocated_amount
        portfolio.allocated_capital += position.allocated_amount
        portfolio.active_positions.append(position)

        logger.info(
            "Position OPENED: opp=%d alloc=$%.2f available=$%.2f allocated=$%.2f",
            position.opportunity_id, position.allocated_amount,
            portfolio.available_capital, portfolio.allocated_capital,
        )
        return portfolio

    def close_position_success(
        self,
        portfolio: PortfolioState,
        opportunity_id: int,
        actual_profit: float,
    ) -> PortfolioState:
        """Close a winning position — realize profit."""
        position = self._find_open_position(portfolio, opportunity_id)
        if position is None:
            logger.warning("No open position for opportunity %d", opportunity_id)
            return portfolio

        position.status = PositionStatus.CLOSED
        position.actual_profit = actual_profit

        # Move capital back: allocated → available + profit
        portfolio.allocated_capital -= position.allocated_amount
        portfolio.available_capital += position.allocated_amount + actual_profit
        portfolio.total_capital += actual_profit
        portfolio.realized_profit += actual_profit

        # Stats
        portfolio.total_trades += 1
        portfolio.winning_trades += 1
        portfolio.consecutive_losses = 0  # Reset streak

        # High-water mark
        if portfolio.total_capital > portfolio.peak_capital:
            portfolio.peak_capital = portfolio.total_capital

        self._update_drawdown(portfolio)

        logger.info(
            "Position CLOSED (win): opp=%d profit=$%.2f total=$%.2f win_rate=%.0f%%",
            opportunity_id, actual_profit, portfolio.total_capital,
            portfolio.win_rate * 100,
        )
        return portfolio

    def close_position_failure(
        self,
        portfolio: PortfolioState,
        opportunity_id: int,
        loss_amount: float,
    ) -> PortfolioState:
        """Close a losing position — realize loss.

        loss_amount should be positive (the amount lost).
        """
        position = self._find_open_position(portfolio, opportunity_id)
        if position is None:
            logger.warning("No open position for opportunity %d", opportunity_id)
            return portfolio

        position.status = PositionStatus.FAILED
        position.actual_profit = -loss_amount

        # Move remaining capital back: allocated → available (minus loss)
        portfolio.allocated_capital -= position.allocated_amount
        returned = max(0.0, position.allocated_amount - loss_amount)
        portfolio.available_capital += returned
        portfolio.total_capital -= loss_amount
        portfolio.realized_profit -= loss_amount

        # Stats
        portfolio.total_trades += 1
        portfolio.losing_trades += 1
        portfolio.consecutive_losses += 1
        portfolio.last_loss_at = datetime.now(timezone.utc)

        # Daily loss tracking
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if portfolio.daily_loss_reset_date != today:
            portfolio.daily_loss_usd = 0.0
            portfolio.daily_loss_reset_date = today
        portfolio.daily_loss_usd += loss_amount

        self._update_drawdown(portfolio)

        logger.warning(
            "Position CLOSED (loss): opp=%d loss=$%.2f total=$%.2f "
            "drawdown=%.1f%% consecutive=%d",
            opportunity_id, loss_amount, portfolio.total_capital,
            portfolio.drawdown_pct * 100, portfolio.consecutive_losses,
        )
        return portfolio

    def _find_open_position(
        self, portfolio: PortfolioState, opportunity_id: int,
    ) -> Position | None:
        for p in portfolio.active_positions:
            if p.opportunity_id == opportunity_id and p.status == PositionStatus.OPEN:
                return p
        return None

    def _update_drawdown(self, portfolio: PortfolioState) -> None:
        """Recalculate drawdown from peak."""
        if portfolio.peak_capital > 0:
            portfolio.drawdown_pct = max(
                0.0,
                (portfolio.peak_capital - portfolio.total_capital) / portfolio.peak_capital,
            )
        else:
            portfolio.drawdown_pct = 0.0

        portfolio.max_drawdown_pct = max(
            portfolio.max_drawdown_pct, portfolio.drawdown_pct,
        )
