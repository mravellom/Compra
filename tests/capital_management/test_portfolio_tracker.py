"""
Tests — Portfolio Tracker: capital evolution on trade success/failure.
"""
import pytest
from datetime import datetime, timezone

from capital_management.models import PortfolioState, Position, PositionStatus
from capital_management.portfolio_tracker import PortfolioTracker


def _portfolio(
    total: float = 10000,
    available: float = 10000,
    positions: list[Position] | None = None,
) -> PortfolioState:
    return PortfolioState(
        total_capital=total,
        available_capital=available,
        allocated_capital=total - available,
        peak_capital=total,
        active_positions=positions or [],
    )


def _position(
    opp_id: int = 1,
    amount: float = 1000,
) -> Position:
    return Position(
        opportunity_id=opp_id,
        allocated_amount=amount,
        entry_price=amount,
        expected_profit=100,
        expected_roi=0.10,
        risk_score=30,
        status=PositionStatus.OPEN,
    )


class TestOpenPosition:

    def test_capital_moves_to_allocated(self):
        tracker = PortfolioTracker()
        portfolio = _portfolio(total=10000, available=10000)
        pos = _position(amount=2000)

        tracker.open_position(portfolio, pos)

        assert portfolio.available_capital == 8000
        assert portfolio.allocated_capital == 2000
        assert len(portfolio.active_positions) == 1
        assert pos.status == PositionStatus.OPEN

    def test_insufficient_capital_raises(self):
        tracker = PortfolioTracker()
        portfolio = _portfolio(total=100, available=100)
        pos = _position(amount=500)

        with pytest.raises(ValueError, match="Insufficient capital"):
            tracker.open_position(portfolio, pos)

    def test_multiple_positions(self):
        tracker = PortfolioTracker()
        portfolio = _portfolio(total=10000, available=10000)

        tracker.open_position(portfolio, _position(opp_id=1, amount=2000))
        tracker.open_position(portfolio, _position(opp_id=2, amount=3000))

        assert portfolio.available_capital == 5000
        assert portfolio.allocated_capital == 5000
        assert len(portfolio.active_positions) == 2


class TestClosePositionSuccess:

    def test_realizes_profit(self):
        tracker = PortfolioTracker()
        pos = _position(opp_id=1, amount=2000)
        portfolio = _portfolio(total=10000, available=8000, positions=[pos])
        portfolio.allocated_capital = 2000

        tracker.close_position_success(portfolio, opportunity_id=1, actual_profit=300)

        assert portfolio.total_capital == 10300
        assert portfolio.available_capital == 10300  # 8000 + 2000 + 300
        assert portfolio.allocated_capital == 0
        assert portfolio.realized_profit == 300
        assert portfolio.winning_trades == 1
        assert portfolio.consecutive_losses == 0
        assert pos.status == PositionStatus.CLOSED

    def test_updates_high_water_mark(self):
        tracker = PortfolioTracker()
        pos = _position(opp_id=1, amount=2000)
        portfolio = _portfolio(total=10000, available=8000, positions=[pos])
        portfolio.allocated_capital = 2000

        tracker.close_position_success(portfolio, 1, 500)

        assert portfolio.peak_capital == 10500
        assert portfolio.drawdown_pct == 0.0

    def test_resets_consecutive_losses(self):
        tracker = PortfolioTracker()
        pos = _position(opp_id=1, amount=1000)
        portfolio = _portfolio(total=9000, available=8000, positions=[pos])
        portfolio.allocated_capital = 1000
        portfolio.consecutive_losses = 5

        tracker.close_position_success(portfolio, 1, 200)

        assert portfolio.consecutive_losses == 0


class TestClosePositionFailure:

    def test_realizes_loss(self):
        tracker = PortfolioTracker()
        pos = _position(opp_id=1, amount=2000)
        portfolio = _portfolio(total=10000, available=8000, positions=[pos])
        portfolio.allocated_capital = 2000

        tracker.close_position_failure(portfolio, opportunity_id=1, loss_amount=500)

        assert portfolio.total_capital == 9500
        assert portfolio.available_capital == 9500  # 8000 + (2000 - 500)
        assert portfolio.allocated_capital == 0
        assert portfolio.realized_profit == -500
        assert portfolio.losing_trades == 1
        assert pos.status == PositionStatus.FAILED

    def test_increments_consecutive_losses(self):
        tracker = PortfolioTracker()
        pos = _position(opp_id=1, amount=1000)
        portfolio = _portfolio(total=10000, available=9000, positions=[pos])
        portfolio.allocated_capital = 1000

        tracker.close_position_failure(portfolio, 1, 200)

        assert portfolio.consecutive_losses == 1
        assert portfolio.last_loss_at is not None

    def test_updates_drawdown(self):
        tracker = PortfolioTracker()
        pos = _position(opp_id=1, amount=2000)
        portfolio = _portfolio(total=10000, available=8000, positions=[pos])
        portfolio.allocated_capital = 2000
        portfolio.peak_capital = 10000

        tracker.close_position_failure(portfolio, 1, 1000)

        # Peak 10000, current 9000 → drawdown 10%
        assert portfolio.drawdown_pct == pytest.approx(0.10, abs=0.01)
        assert portfolio.max_drawdown_pct == pytest.approx(0.10, abs=0.01)

    def test_tracks_daily_loss(self):
        tracker = PortfolioTracker()
        pos = _position(opp_id=1, amount=1000)
        portfolio = _portfolio(total=10000, available=9000, positions=[pos])
        portfolio.allocated_capital = 1000

        tracker.close_position_failure(portfolio, 1, 300)

        assert portfolio.daily_loss_usd == 300
        assert portfolio.daily_loss_reset_date is not None

    def test_total_loss_caps_at_allocation(self):
        """Even if loss exceeds allocation, available capital doesn't go negative."""
        tracker = PortfolioTracker()
        pos = _position(opp_id=1, amount=500)
        portfolio = _portfolio(total=10000, available=9500, positions=[pos])
        portfolio.allocated_capital = 500

        tracker.close_position_failure(portfolio, 1, 600)

        # Returned = max(0, 500-600) = 0
        assert portfolio.available_capital == 9500 + 0
        assert portfolio.total_capital == 9400


class TestFullLifecycle:

    def test_win_then_loss(self):
        tracker = PortfolioTracker()
        portfolio = _portfolio(total=10000, available=10000)

        # Open and win
        pos1 = _position(opp_id=1, amount=2000)
        tracker.open_position(portfolio, pos1)
        tracker.close_position_success(portfolio, 1, 400)

        assert portfolio.total_capital == 10400
        assert portfolio.winning_trades == 1
        assert portfolio.consecutive_losses == 0

        # Open and lose
        pos2 = _position(opp_id=2, amount=1500)
        tracker.open_position(portfolio, pos2)
        tracker.close_position_failure(portfolio, 2, 300)

        assert portfolio.total_capital == 10100
        assert portfolio.losing_trades == 1
        assert portfolio.consecutive_losses == 1
        assert portfolio.win_rate == 0.5
        assert portfolio.total_trades == 2

    def test_multiple_consecutive_losses(self):
        tracker = PortfolioTracker()
        portfolio = _portfolio(total=10000, available=10000)

        for i in range(4):
            pos = _position(opp_id=i, amount=500)
            tracker.open_position(portfolio, pos)
            tracker.close_position_failure(portfolio, i, 100)

        assert portfolio.consecutive_losses == 4
        assert portfolio.total_capital == 9600
        assert portfolio.drawdown_pct == pytest.approx(0.04, abs=0.01)


class TestPortfolioProperties:

    def test_win_rate(self):
        p = PortfolioState(total_capital=10000, available_capital=10000)
        p.total_trades = 10
        p.winning_trades = 7
        assert p.win_rate == pytest.approx(0.7, abs=0.01)

    def test_avg_profit_per_trade(self):
        p = PortfolioState(total_capital=10000, available_capital=10000)
        p.total_trades = 5
        p.realized_profit = 250
        assert p.avg_profit_per_trade == 50.0

    def test_allocation_pct(self):
        p = PortfolioState(total_capital=10000, available_capital=7000, allocated_capital=3000)
        assert p.allocation_pct == pytest.approx(0.30, abs=0.01)

    def test_zero_trades_safe(self):
        p = PortfolioState(total_capital=10000, available_capital=10000)
        assert p.win_rate == 0.0
        assert p.avg_profit_per_trade == 0.0
