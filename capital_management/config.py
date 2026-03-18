"""
Configuration for the Capital Management System.

Three strategy modes control all thresholds simultaneously.
Every value overridable via environment or direct injection.
"""
import os
from dataclasses import dataclass
from enum import Enum


class StrategyMode(Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


@dataclass(frozen=True)
class PositionSizingConfig:
    """Controls how much capital to allocate per trade."""

    max_allocation_pct_per_trade: float   # Max % of total capital per single trade
    max_allocation_pct_per_category: float
    min_allocation_usd: float             # Below this → reject (not worth the overhead)
    risk_adjustment_factor: float         # How aggressively risk reduces sizing (0-1)


@dataclass(frozen=True)
class DiversificationConfig:
    """Concentration limits to prevent overexposure."""

    max_pct_per_product: float         # Max % of allocated capital in one product
    max_pct_per_marketplace: float     # Max % in one marketplace
    max_pct_per_category: float        # Max % in one category
    max_open_positions: int            # Hard cap on simultaneous positions


@dataclass(frozen=True)
class RiskConfig:
    """Risk management thresholds — circuit breakers for capital protection."""

    max_drawdown_pct: float            # Portfolio drawdown halt (e.g. 0.20 = 20%)
    capital_stop_loss_pct: float       # Absolute capital loss halt
    consecutive_loss_limit: int        # After N consecutive losses → reduce sizing
    sizing_reduction_on_streak: float  # Multiplier when loss streak active (e.g. 0.50)
    cooldown_after_loss_minutes: float # Minimum wait after a loss before next trade
    max_daily_loss_usd: float          # Daily loss cap


@dataclass(frozen=True)
class CapitalConfig:
    """Master config assembling all sub-configs."""

    strategy: StrategyMode
    initial_capital: float
    position_sizing: PositionSizingConfig
    diversification: DiversificationConfig
    risk: RiskConfig


# ── Strategy Presets ─────────────────────────────────────────

_CONSERVATIVE = CapitalConfig(
    strategy=StrategyMode.CONSERVATIVE,
    initial_capital=float(os.getenv("INITIAL_CAPITAL", "5000")),
    position_sizing=PositionSizingConfig(
        max_allocation_pct_per_trade=0.10,
        max_allocation_pct_per_category=0.25,
        min_allocation_usd=30.0,
        risk_adjustment_factor=0.8,
    ),
    diversification=DiversificationConfig(
        max_pct_per_product=0.15,
        max_pct_per_marketplace=0.35,
        max_pct_per_category=0.30,
        max_open_positions=8,
    ),
    risk=RiskConfig(
        max_drawdown_pct=0.10,
        capital_stop_loss_pct=0.08,
        consecutive_loss_limit=2,
        sizing_reduction_on_streak=0.40,
        cooldown_after_loss_minutes=120,
        max_daily_loss_usd=200.0,
    ),
)

_BALANCED = CapitalConfig(
    strategy=StrategyMode.BALANCED,
    initial_capital=float(os.getenv("INITIAL_CAPITAL", "5000")),
    position_sizing=PositionSizingConfig(
        max_allocation_pct_per_trade=0.20,
        max_allocation_pct_per_category=0.40,
        min_allocation_usd=20.0,
        risk_adjustment_factor=0.6,
    ),
    diversification=DiversificationConfig(
        max_pct_per_product=0.25,
        max_pct_per_marketplace=0.45,
        max_pct_per_category=0.40,
        max_open_positions=15,
    ),
    risk=RiskConfig(
        max_drawdown_pct=0.20,
        capital_stop_loss_pct=0.15,
        consecutive_loss_limit=3,
        sizing_reduction_on_streak=0.50,
        cooldown_after_loss_minutes=60,
        max_daily_loss_usd=500.0,
    ),
)

_AGGRESSIVE = CapitalConfig(
    strategy=StrategyMode.AGGRESSIVE,
    initial_capital=float(os.getenv("INITIAL_CAPITAL", "5000")),
    position_sizing=PositionSizingConfig(
        max_allocation_pct_per_trade=0.35,
        max_allocation_pct_per_category=0.60,
        min_allocation_usd=10.0,
        risk_adjustment_factor=0.4,
    ),
    diversification=DiversificationConfig(
        max_pct_per_product=0.35,
        max_pct_per_marketplace=0.60,
        max_pct_per_category=0.50,
        max_open_positions=25,
    ),
    risk=RiskConfig(
        max_drawdown_pct=0.30,
        capital_stop_loss_pct=0.25,
        consecutive_loss_limit=5,
        sizing_reduction_on_streak=0.70,
        cooldown_after_loss_minutes=15,
        max_daily_loss_usd=1000.0,
    ),
)

STRATEGY_PRESETS: dict[StrategyMode, CapitalConfig] = {
    StrategyMode.CONSERVATIVE: _CONSERVATIVE,
    StrategyMode.BALANCED: _BALANCED,
    StrategyMode.AGGRESSIVE: _AGGRESSIVE,
}


def get_config(
    mode: str | StrategyMode | None = None,
) -> CapitalConfig:
    """Get capital config for a strategy mode.

    Reads CAPITAL_STRATEGY env var if mode not provided.
    Defaults to BALANCED.
    """
    if mode is None:
        mode = os.getenv("CAPITAL_STRATEGY", "balanced")

    if isinstance(mode, str):
        mode = StrategyMode(mode.lower())

    return STRATEGY_PRESETS[mode]
