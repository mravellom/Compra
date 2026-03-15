"""Tests for the trend detection domain layer."""
import pytest
from datetime import datetime, timezone, timedelta

from engines.trend.domain.enums import TrendDirection, TrendStrength, TrendType
from engines.trend.domain.models import ProductTrendResult, TrendSignal, TrendSnapshot
from engines.trend.domain.strategies import PriceMomentumStrategy, VelocitySpikeStrategy
from engines.trend.application.trend_aggregator import TrendAggregator


def _make_snapshots(
    count: int = 10,
    velocity_7d_start: float = 1.0,
    velocity_7d_growth: float = 0.0,
    price_start: float = 100.0,
    price_growth: float = 0.0,
    listing_count_start: int = 10,
    listing_growth: int = 0,
) -> list[TrendSnapshot]:
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    snapshots = []
    for i in range(count):
        snapshots.append(TrendSnapshot(
            product_id=1,
            listing_count=listing_count_start + i * listing_growth,
            avg_price=price_start + i * price_growth,
            seller_count=5,
            marketplace_count=3,
            velocity_7d=velocity_7d_start + i * velocity_7d_growth,
            velocity_30d=velocity_7d_start,  # baseline stays constant
            snapshot_at=base + timedelta(days=i),
        ))
    return snapshots


# ─── VelocitySpikeStrategy ──────────────────────────────────────────

class TestVelocitySpikeStrategy:
    def setup_method(self):
        self.strategy = VelocitySpikeStrategy()

    def test_stable_velocity(self):
        snapshots = _make_snapshots(velocity_7d_start=5.0, velocity_7d_growth=0)
        signal = self.strategy.detect(1, snapshots)
        assert signal.direction == TrendDirection.STABLE

    def test_rising_velocity(self):
        snapshots = _make_snapshots(
            velocity_7d_start=5.0, velocity_7d_growth=2.0,
            listing_count_start=10, listing_growth=5,
        )
        signal = self.strategy.detect(1, snapshots)
        assert signal.direction == TrendDirection.RISING
        assert signal.velocity_ratio > 1.0

    def test_falling_velocity(self):
        # Latest velocity much lower than 30d baseline
        snapshots = _make_snapshots(velocity_7d_start=10.0, velocity_7d_growth=-1.5)
        # Force last snapshot to have very low velocity
        snapshots[-1].velocity_7d = 1.0
        snapshots[-1].velocity_30d = 10.0
        signal = self.strategy.detect(1, snapshots)
        assert signal.direction == TrendDirection.FALLING

    def test_empty_snapshots(self):
        signal = self.strategy.detect(1, [])
        assert signal.direction == TrendDirection.STABLE
        assert signal.score == 0

    def test_single_snapshot(self):
        snapshots = _make_snapshots(count=1)
        signal = self.strategy.detect(1, snapshots)
        assert signal.direction == TrendDirection.STABLE

    def test_strong_spike(self):
        snapshots = _make_snapshots(
            velocity_7d_start=1.0, velocity_7d_growth=5.0,
            listing_count_start=5, listing_growth=20,
        )
        signal = self.strategy.detect(1, snapshots)
        assert signal.strength in (TrendStrength.MODERATE, TrendStrength.STRONG)
        assert signal.score > 40

    def test_weight(self):
        assert self.strategy.weight == 0.5


# ─── PriceMomentumStrategy ─────────────────────────────────────────

class TestPriceMomentumStrategy:
    def setup_method(self):
        self.strategy = PriceMomentumStrategy()

    def test_stable_prices(self):
        snapshots = _make_snapshots(price_start=100, price_growth=0)
        signal = self.strategy.detect(1, snapshots)
        assert signal.direction == TrendDirection.STABLE

    def test_rising_prices(self):
        snapshots = _make_snapshots(price_start=100, price_growth=5)
        signal = self.strategy.detect(1, snapshots)
        assert signal.direction == TrendDirection.RISING
        assert signal.price_momentum > 0

    def test_falling_prices(self):
        snapshots = _make_snapshots(price_start=200, price_growth=-10)
        signal = self.strategy.detect(1, snapshots)
        assert signal.direction == TrendDirection.FALLING
        assert signal.price_momentum < 0

    def test_insufficient_data(self):
        snapshots = _make_snapshots(count=2)
        signal = self.strategy.detect(1, snapshots)
        # Only 2 snapshots, needs 3
        assert signal.score == 0

    def test_weight(self):
        assert self.strategy.weight == 0.5


# ─── TrendAggregator ───────────────────────────────────────────────

class TestTrendAggregator:
    def setup_method(self):
        self.aggregator = TrendAggregator([
            VelocitySpikeStrategy(),
            PriceMomentumStrategy(),
        ])

    def test_aggregate_stable(self):
        snapshots = _make_snapshots(price_growth=0, velocity_7d_growth=0)
        result = self.aggregator.aggregate(1, snapshots)
        assert result.trend_type in ("stable", "rising", "falling")
        assert isinstance(result.trend_score, float)

    def test_aggregate_rising(self):
        snapshots = _make_snapshots(
            price_growth=10, velocity_7d_growth=3.0,
            listing_count_start=5, listing_growth=10,
        )
        result = self.aggregator.aggregate(1, snapshots)
        assert result.trend_score > 30

    def test_aggregate_empty(self):
        result = self.aggregator.aggregate(1, [])
        assert result.trend_score == 0

    def test_breakout_detection(self):
        snapshots = _make_snapshots(
            count=15,
            price_growth=15,  # Strong price rise
            velocity_7d_start=1.0, velocity_7d_growth=5.0,  # Velocity spike
            listing_count_start=5, listing_growth=20,  # Volume surge
        )
        result = self.aggregator.aggregate(1, snapshots)
        assert result.trend_score > 50

    def test_signals_dict_populated(self):
        snapshots = _make_snapshots(count=5, price_growth=2)
        result = self.aggregator.aggregate(1, snapshots)
        assert "velocity_spike" in result.signals
        assert "price_momentum" in result.signals
