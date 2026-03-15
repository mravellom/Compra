"""Trend detection strategies.

Each strategy analyzes product snapshots and produces a TrendSignal.
The TrendAggregator combines signals from multiple strategies.
"""
import logging
import math
from abc import ABC, abstractmethod

import numpy as np

from .enums import TrendDirection, TrendStrength
from .models import TrendSignal, TrendSnapshot

logger = logging.getLogger(__name__)


class TrendStrategy(ABC):
    """Abstract base for trend detection."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def weight(self) -> float:
        """Strategy weight in composite scoring (0-1)."""
        ...

    @abstractmethod
    def detect(self, product_id: int, snapshots: list[TrendSnapshot]) -> TrendSignal:
        ...


class VelocitySpikeStrategy(TrendStrategy):
    """Detects trends from listing velocity changes.

    Compares recent listing velocity (7d) against historical (30d).
    A high ratio indicates a demand spike.
    """

    @property
    def name(self) -> str:
        return "velocity_spike"

    @property
    def weight(self) -> float:
        return 0.5

    def detect(self, product_id: int, snapshots: list[TrendSnapshot]) -> TrendSignal:
        if len(snapshots) < 2:
            return TrendSignal(product_id=product_id)

        latest = snapshots[-1]
        velocity_7d = latest.velocity_7d
        velocity_30d = latest.velocity_30d

        # Velocity ratio: how much faster is recent activity vs baseline
        ratio = velocity_7d / (velocity_30d + 1e-10) if velocity_30d > 0 else velocity_7d

        # Volume change: compare recent vs older listing counts
        recent_counts = [s.listing_count for s in snapshots[-3:]]
        older_counts = [s.listing_count for s in snapshots[:max(len(snapshots) - 3, 1)]]
        vol_change = (np.mean(recent_counts) - np.mean(older_counts)) / (np.mean(older_counts) + 1e-10)

        # Direction
        if ratio > 1.5:
            direction = TrendDirection.RISING
        elif ratio < 0.5:
            direction = TrendDirection.FALLING
        else:
            direction = TrendDirection.STABLE

        # Strength
        if ratio > 3.0 or abs(vol_change) > 1.0:
            strength = TrendStrength.STRONG
        elif ratio > 1.8 or abs(vol_change) > 0.5:
            strength = TrendStrength.MODERATE
        else:
            strength = TrendStrength.WEAK

        # Score (0-100): based on velocity ratio magnitude
        score = min(100, max(0, (ratio - 1.0) * 40 + vol_change * 20 + 30))

        return TrendSignal(
            product_id=product_id,
            direction=direction,
            strength=strength,
            score=round(score, 1),
            velocity_ratio=round(ratio, 3),
            volume_change=round(vol_change, 3),
            metadata={"strategy": self.name, "velocity_7d": velocity_7d, "velocity_30d": velocity_30d},
        )


class PriceMomentumStrategy(TrendStrategy):
    """Detects trends from price acceleration.

    Computes price rate of change and its acceleration (second derivative).
    Identifies breakout conditions when acceleration exceeds thresholds.
    """

    @property
    def name(self) -> str:
        return "price_momentum"

    @property
    def weight(self) -> float:
        return 0.5

    def detect(self, product_id: int, snapshots: list[TrendSnapshot]) -> TrendSignal:
        if len(snapshots) < 3:
            return TrendSignal(product_id=product_id)

        prices = [s.avg_price for s in snapshots if s.avg_price > 0]
        if len(prices) < 3:
            return TrendSignal(product_id=product_id)

        prices_arr = np.array(prices)

        # First derivative: rate of change (normalized)
        returns = np.diff(prices_arr) / (prices_arr[:-1] + 1e-10)
        recent_return = float(np.mean(returns[-3:])) if len(returns) >= 3 else float(returns[-1])
        overall_return = float(np.mean(returns))

        # Second derivative: acceleration
        if len(returns) >= 2:
            acceleration = float(np.diff(returns)[-1])
        else:
            acceleration = 0.0

        # Momentum: weighted recent vs overall return
        momentum = 0.7 * recent_return + 0.3 * overall_return

        # Direction
        if momentum > 0.02:
            direction = TrendDirection.RISING
        elif momentum < -0.02:
            direction = TrendDirection.FALLING
        else:
            direction = TrendDirection.STABLE

        # Strength from acceleration magnitude
        abs_accel = abs(acceleration)
        if abs_accel > 0.05 or abs(momentum) > 0.1:
            strength = TrendStrength.STRONG
        elif abs_accel > 0.02 or abs(momentum) > 0.05:
            strength = TrendStrength.MODERATE
        else:
            strength = TrendStrength.WEAK

        # Score (0-100)
        momentum_component = min(50, abs(momentum) * 500)
        accel_component = min(50, abs_accel * 1000)
        score = max(0, min(100, momentum_component + accel_component))

        return TrendSignal(
            product_id=product_id,
            direction=direction,
            strength=strength,
            score=round(score, 1),
            price_momentum=round(momentum, 5),
            metadata={
                "strategy": self.name,
                "recent_return": round(recent_return, 5),
                "acceleration": round(acceleration, 5),
                "data_points": len(prices),
            },
        )
