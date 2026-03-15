"""Aggregates signals from multiple trend strategies into a composite result."""
import logging
from typing import Optional

from ..domain.enums import TrendDirection, TrendStrength, TrendType
from ..domain.models import ProductTrendResult, TrendSignal, TrendSnapshot
from ..domain.strategies import TrendStrategy

logger = logging.getLogger(__name__)


class TrendAggregator:
    """Combines signals from multiple TrendStrategy instances."""

    def __init__(self, strategies: list[TrendStrategy]) -> None:
        self._strategies = strategies

    def aggregate(
        self, product_id: int, snapshots: list[TrendSnapshot]
    ) -> ProductTrendResult:
        """Run all strategies and combine into a single trend result."""
        if not snapshots:
            return ProductTrendResult(product_id=product_id)

        signals: list[TrendSignal] = []
        signal_details: dict[str, dict] = {}

        for strategy in self._strategies:
            try:
                signal = strategy.detect(product_id, snapshots)
                signals.append(signal)
                signal_details[strategy.name] = {
                    "direction": signal.direction.value,
                    "strength": signal.strength.value,
                    "score": signal.score,
                }
            except Exception:
                logger.error(
                    "Strategy %s failed for product %d",
                    strategy.name, product_id, exc_info=True,
                )

        if not signals:
            return ProductTrendResult(product_id=product_id)

        # Weighted composite score
        total_weight = sum(s.weight for s in self._strategies[:len(signals)])
        composite_score = sum(
            sig.score * strat.weight
            for sig, strat in zip(signals, self._strategies)
        ) / (total_weight or 1)

        # Weighted velocity ratio and momentum
        velocity_ratio = sum(
            sig.velocity_ratio * strat.weight
            for sig, strat in zip(signals, self._strategies)
        ) / (total_weight or 1)

        price_momentum = sum(
            sig.price_momentum * strat.weight
            for sig, strat in zip(signals, self._strategies)
        ) / (total_weight or 1)

        volume_change = sum(
            sig.volume_change * strat.weight
            for sig, strat in zip(signals, self._strategies)
        ) / (total_weight or 1)

        # Determine composite direction (majority vote, weighted)
        direction_scores: dict[TrendDirection, float] = {}
        for sig, strat in zip(signals, self._strategies):
            direction_scores[sig.direction] = (
                direction_scores.get(sig.direction, 0) + strat.weight
            )
        composite_direction = max(direction_scores, key=direction_scores.get)  # type: ignore[arg-type]

        # Determine trend type
        trend_type = self._classify_trend_type(
            composite_direction, composite_score, velocity_ratio
        )

        # Determine strength
        trend_strength = self._classify_strength(composite_score)

        return ProductTrendResult(
            product_id=product_id,
            trend_score=round(composite_score, 1),
            velocity_ratio=round(velocity_ratio, 3),
            price_momentum=round(price_momentum, 5),
            volume_change=round(volume_change, 3),
            trend_type=trend_type.value,
            trend_strength=trend_strength.value,
            signals=signal_details,
        )

    @staticmethod
    def _classify_trend_type(
        direction: TrendDirection, score: float, velocity_ratio: float
    ) -> TrendType:
        if score >= 70 and velocity_ratio > 2.5:
            return TrendType.BREAKOUT
        if direction == TrendDirection.RISING:
            return TrendType.RISING
        if direction == TrendDirection.FALLING:
            return TrendType.FALLING
        return TrendType.STABLE

    @staticmethod
    def _classify_strength(score: float) -> TrendStrength:
        if score >= 70:
            return TrendStrength.STRONG
        if score >= 40:
            return TrendStrength.MODERATE
        return TrendStrength.WEAK
