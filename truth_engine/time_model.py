"""
Time-to-Sell Model — estimates how long an opportunity takes to convert.

Uses sales velocity, reviews, and price competitiveness to predict sale speed.
Applies time decay to profit: slow-selling items are worth less.
"""
import logging
import math
from dataclasses import dataclass

from .config import TimeModelWeights, TruthEngineConfig

logger = logging.getLogger(__name__)


@dataclass
class TimeEstimate:
    """Result of time-to-sell estimation."""

    expected_days: float
    time_decay: float             # 0-1 multiplier on profit
    adjusted_profit: float        # profit * time_decay
    velocity_signal: float        # 0-1 normalized velocity
    reviews_signal: float         # 0-1 normalized reviews
    competitiveness_signal: float # 0-1 normalized price position


class TimeModel:
    """Estimates time-to-sell for an opportunity."""

    def __init__(
        self,
        config: TruthEngineConfig | None = None,
        weights: TimeModelWeights | None = None,
    ) -> None:
        self._cfg = config or TruthEngineConfig()
        self._w = weights or TimeModelWeights()

    def estimate_time_to_sell(
        self,
        estimated_monthly_sales: float,
        reviews_count: int,
        competitor_count: int,
        price_vs_lowest_pct: float,
        profit: float,
    ) -> TimeEstimate:
        """Estimate days to sell and apply time decay to profit.

        Args:
            estimated_monthly_sales: Expected sales/month for this product.
            reviews_count: Total reviews across sell-side listings.
            competitor_count: Number of active competitors.
            price_vs_lowest_pct: Our sell price / lowest competitor price (1.0 = at parity).
            profit: Pre-adjustment profit to apply time decay to.

        Returns:
            TimeEstimate with expected_days, time_decay, and adjusted_profit.
        """
        # 1. Velocity signal (0-1): higher monthly sales = faster
        #    30 sales/month → 1.0, 0 sales → 0.0
        velocity_signal = min(1.0, estimated_monthly_sales / 30.0)

        # 2. Reviews signal (0-1): more reviews = more demand
        #    200 reviews → 1.0, 0 → 0.0
        reviews_signal = min(1.0, reviews_count / 200.0)

        # 3. Competitiveness signal (0-1): lower price vs competitors = faster
        #    At or below lowest → 1.0, 20% above → 0.0
        if price_vs_lowest_pct <= 1.0:
            competitiveness_signal = 1.0
        elif price_vs_lowest_pct >= 1.20:
            competitiveness_signal = 0.0
        else:
            competitiveness_signal = 1.0 - (price_vs_lowest_pct - 1.0) / 0.20

        # Composite speed score (0-1): weighted average
        speed = (
            velocity_signal * self._w.velocity_weight
            + reviews_signal * self._w.reviews_weight
            + competitiveness_signal * self._w.competitiveness_weight
        )

        # Map speed to days: 1.0 → fast_sale_days, 0.0 → slow_sale_days
        expected_days = (
            self._cfg.slow_sale_days
            - speed * (self._cfg.slow_sale_days - self._cfg.fast_sale_days)
        )
        expected_days = max(self._cfg.fast_sale_days, expected_days)

        # Time decay: 1 / (1 + expected_days_to_sell)
        if self._cfg.time_decay_enabled:
            time_decay = 1.0 / (1.0 + expected_days)
        else:
            time_decay = 1.0

        adjusted_profit = profit * time_decay

        logger.debug(
            "time_model: speed=%.2f days=%.1f decay=%.3f profit=%.2f→%.2f "
            "(vel=%.2f rev=%.2f comp=%.2f)",
            speed, expected_days, time_decay, profit, adjusted_profit,
            velocity_signal, reviews_signal, competitiveness_signal,
        )

        return TimeEstimate(
            expected_days=round(expected_days, 2),
            time_decay=round(time_decay, 4),
            adjusted_profit=round(adjusted_profit, 2),
            velocity_signal=round(velocity_signal, 3),
            reviews_signal=round(reviews_signal, 3),
            competitiveness_signal=round(competitiveness_signal, 3),
        )
