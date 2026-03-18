"""
Configuration for the Execution Realism Layer.

Every threshold is configurable. Defaults are conservative — false rejections
are cheaper than false executions.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class PriceValidationConfig:
    """Thresholds for real-time price re-check."""

    max_price_deviation_pct: float = float(os.getenv("MAX_PRICE_DEVIATION", "0.05"))
    min_profit_after_recheck_usd: float = float(os.getenv("MIN_PROFIT_RECHECK", "5.0"))


@dataclass(frozen=True)
class StockValidationConfig:
    """Thresholds for stock availability."""

    low_stock_threshold: int = int(os.getenv("LOW_STOCK_THRESHOLD", "2"))
    low_stock_confidence_penalty: float = 0.15          # reduce confidence by 15%
    frequent_stockout_penalty: float = 0.20             # penalty for unreliable sellers
    frequent_stockout_threshold: int = 3                # stockouts in window → penalize


@dataclass(frozen=True)
class LatencyConfig:
    """Thresholds for pipeline latency."""

    max_latency_seconds: float = float(os.getenv("MAX_LATENCY_SECONDS", "300"))  # 5 min
    warning_latency_seconds: float = 120.0
    latency_penalty_per_minute: float = 0.03            # 3% penalty per minute over warning


@dataclass(frozen=True)
class SlippageConfig:
    """Thresholds for slippage estimation."""

    base_slippage_pct: float = 0.01                     # 1% baseline slippage
    high_volatility_multiplier: float = 2.5
    high_demand_multiplier: float = 1.5
    min_profit_after_slippage_usd: float = float(os.getenv("MIN_PROFIT_SLIPPAGE", "5.0"))


@dataclass(frozen=True)
class CompetitionConfig:
    """Thresholds for competition scoring."""

    high_roi_threshold: float = 0.30                    # ROI > 30% → competition likely
    high_roi_score: float = 0.30
    popular_product_reviews_threshold: int = 200
    popular_product_score: float = 0.25
    high_velocity_threshold: float = 15.0               # monthly sales
    high_velocity_score: float = 0.20
    competition_weight_on_p_sale: float = 0.40          # how much competition reduces P(sale)


@dataclass(frozen=True)
class ExecutionRealismConfig:
    """Master config assembling all sub-configs."""

    price: PriceValidationConfig = PriceValidationConfig()
    stock: StockValidationConfig = StockValidationConfig()
    latency: LatencyConfig = LatencyConfig()
    slippage: SlippageConfig = SlippageConfig()
    competition: CompetitionConfig = CompetitionConfig()
    min_execution_confidence: float = float(os.getenv("MIN_EXEC_CONFIDENCE", "0.30"))
