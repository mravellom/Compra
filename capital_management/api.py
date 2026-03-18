"""
Capital Management API — observability and control endpoints.

Exposes:
- GET  /capital/metrics — portfolio state, drawdown, win rate
- GET  /capital/positions — active positions
- GET  /capital/config — current strategy and thresholds
- POST /capital/strategy — switch strategy mode
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .config import CapitalConfig, StrategyMode, get_config
from .models import PortfolioState, PositionStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/capital", tags=["capital-management"])


# ── Singleton portfolio state (process-level) ────────────────

_portfolio_state: PortfolioState | None = None
_current_config: CapitalConfig | None = None


def get_portfolio_state() -> PortfolioState:
    """Get or initialize the global portfolio state."""
    global _portfolio_state, _current_config
    if _portfolio_state is None:
        _current_config = get_config()
        _portfolio_state = PortfolioState(
            total_capital=_current_config.initial_capital,
            available_capital=_current_config.initial_capital,
        )
    return _portfolio_state


def get_capital_config() -> CapitalConfig:
    """Get the current capital config."""
    global _current_config
    if _current_config is None:
        _current_config = get_config()
    return _current_config


def set_portfolio_state(state: PortfolioState) -> None:
    """Replace the global portfolio state (for testing/reset)."""
    global _portfolio_state
    _portfolio_state = state


def set_capital_config(config: CapitalConfig) -> None:
    """Replace the global capital config."""
    global _current_config
    _current_config = config


# ── Response schemas ─────────────────────────────────────────

class CapitalMetricsResponse(BaseModel):
    strategy: str
    total_capital: float
    available_capital: float
    allocated_capital: float
    allocation_pct: float
    realized_profit: float
    drawdown_pct: float
    max_drawdown_pct: float
    consecutive_losses: int
    total_trades: int
    win_rate: float
    avg_profit_per_trade: float
    open_positions: int
    daily_loss_usd: float


class PositionResponse(BaseModel):
    opportunity_id: int
    allocated_amount: float
    entry_price: float
    expected_profit: float
    expected_roi: float
    risk_score: float
    status: str
    marketplace: str = ""
    category: str = ""
    timestamp: datetime


class ConfigResponse(BaseModel):
    strategy: str
    max_allocation_pct_per_trade: float
    max_allocation_pct_per_category: float
    min_allocation_usd: float
    max_drawdown_pct: float
    consecutive_loss_limit: int
    max_open_positions: int
    max_daily_loss_usd: float


class StrategySwitchRequest(BaseModel):
    strategy: str = Field(..., description="conservative, balanced, or aggressive")


# ── Endpoints ────────────────────────────────────────────────

@router.get("/metrics", response_model=CapitalMetricsResponse)
async def capital_metrics():
    """Current portfolio state and performance metrics."""
    portfolio = get_portfolio_state()
    config = get_capital_config()

    return CapitalMetricsResponse(
        strategy=config.strategy.value,
        total_capital=round(portfolio.total_capital, 2),
        available_capital=round(portfolio.available_capital, 2),
        allocated_capital=round(portfolio.allocated_capital, 2),
        allocation_pct=round(portfolio.allocation_pct, 4),
        realized_profit=round(portfolio.realized_profit, 2),
        drawdown_pct=round(portfolio.drawdown_pct, 4),
        max_drawdown_pct=round(portfolio.max_drawdown_pct, 4),
        consecutive_losses=portfolio.consecutive_losses,
        total_trades=portfolio.total_trades,
        win_rate=round(portfolio.win_rate, 4),
        avg_profit_per_trade=round(portfolio.avg_profit_per_trade, 2),
        open_positions=portfolio.open_position_count,
        daily_loss_usd=round(portfolio.daily_loss_usd, 2),
    )


@router.get("/positions", response_model=list[PositionResponse])
async def list_positions(
    status: str | None = Query(None, description="open, closed, or failed"),
):
    """List portfolio positions."""
    portfolio = get_portfolio_state()
    positions = portfolio.active_positions

    if status:
        try:
            target_status = PositionStatus(status)
            positions = [p for p in positions if p.status == target_status]
        except ValueError:
            raise HTTPException(400, f"Invalid status: {status}")

    return [
        PositionResponse(
            opportunity_id=p.opportunity_id,
            allocated_amount=round(p.allocated_amount, 2),
            entry_price=round(p.entry_price, 2),
            expected_profit=round(p.expected_profit, 2),
            expected_roi=round(p.expected_roi, 4),
            risk_score=round(p.risk_score, 1),
            status=p.status.value,
            marketplace=p.marketplace,
            category=p.category,
            timestamp=p.timestamp,
        )
        for p in positions
    ]


@router.get("/config", response_model=ConfigResponse)
async def get_config_endpoint():
    """Current capital management configuration."""
    cfg = get_capital_config()
    return ConfigResponse(
        strategy=cfg.strategy.value,
        max_allocation_pct_per_trade=cfg.position_sizing.max_allocation_pct_per_trade,
        max_allocation_pct_per_category=cfg.position_sizing.max_allocation_pct_per_category,
        min_allocation_usd=cfg.position_sizing.min_allocation_usd,
        max_drawdown_pct=cfg.risk.max_drawdown_pct,
        consecutive_loss_limit=cfg.risk.consecutive_loss_limit,
        max_open_positions=cfg.diversification.max_open_positions,
        max_daily_loss_usd=cfg.risk.max_daily_loss_usd,
    )


@router.post("/strategy")
async def switch_strategy(request: StrategySwitchRequest):
    """Switch the active strategy mode."""
    try:
        mode = StrategyMode(request.strategy.lower())
    except ValueError:
        raise HTTPException(400, f"Invalid strategy: {request.strategy}")

    new_config = get_config(mode)
    set_capital_config(new_config)

    logger.info("Strategy switched to: %s", mode.value)

    return {
        "strategy": mode.value,
        "message": f"Switched to {mode.value} strategy",
    }
