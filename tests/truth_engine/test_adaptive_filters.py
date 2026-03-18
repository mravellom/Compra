"""
Tests — Adaptive Filters: thresholds tighten when precision drops, relax when high.
"""
import pytest

from truth_engine.adaptive_filters import AdaptiveFilterEngine, AdaptiveFilterState
from truth_engine.config import AdaptiveFilterBounds, TruthEngineConfig
from truth_engine.models import PrecisionMetrics


def _metrics(precision: float, total_executed: int = 20) -> PrecisionMetrics:
    return PrecisionMetrics(
        precision=precision,
        total_executed=total_executed,
        total_successful=int(precision * total_executed),
        total_failed=int((1 - precision) * total_executed),
    )


class TestAdaptiveFilterEngine:

    def test_tighten_when_precision_low(self):
        cfg = TruthEngineConfig(precision_target=0.70, min_samples_for_adaptation=5)
        engine = AdaptiveFilterEngine(config=cfg)
        initial_profit = engine.state.min_profit_usd
        initial_roi = engine.state.min_roi

        engine.adapt(_metrics(precision=0.40, total_executed=10))

        assert engine.state.min_profit_usd > initial_profit
        assert engine.state.min_roi > initial_roi
        assert engine.state.tighten_count == 1
        assert engine.state.relax_count == 0

    def test_relax_when_precision_high(self):
        cfg = TruthEngineConfig(precision_target=0.70, min_samples_for_adaptation=5)
        engine = AdaptiveFilterEngine(config=cfg)
        initial_profit = engine.state.min_profit_usd

        engine.adapt(_metrics(precision=0.90, total_executed=10))

        assert engine.state.min_profit_usd < initial_profit
        assert engine.state.relax_count == 1
        assert engine.state.tighten_count == 0

    def test_no_change_at_target(self):
        cfg = TruthEngineConfig(precision_target=0.70, min_samples_for_adaptation=5)
        engine = AdaptiveFilterEngine(config=cfg)
        initial = AdaptiveFilterState(
            min_profit_usd=engine.state.min_profit_usd,
            min_roi=engine.state.min_roi,
        )

        engine.adapt(_metrics(precision=0.70, total_executed=10))

        # Precision == target → no change (neither < nor >)
        assert engine.state.tighten_count == 0
        assert engine.state.relax_count == 0

    def test_skip_adaptation_insufficient_samples(self):
        cfg = TruthEngineConfig(min_samples_for_adaptation=20)
        engine = AdaptiveFilterEngine(config=cfg)
        initial_profit = engine.state.min_profit_usd

        engine.adapt(_metrics(precision=0.20, total_executed=5))

        assert engine.state.min_profit_usd == initial_profit
        assert engine.state.tighten_count == 0

    def test_respects_upper_bounds(self):
        bounds = AdaptiveFilterBounds(
            min_profit_bounds=(5.0, 25.0),
            min_roi_bounds=(0.05, 0.20),
        )
        cfg = TruthEngineConfig(
            precision_target=0.70,
            min_samples_for_adaptation=5,
            adjustment_step=0.50,  # Very aggressive step
        )
        engine = AdaptiveFilterEngine(config=cfg, bounds=bounds)

        # Tighten many times
        for _ in range(20):
            engine.adapt(_metrics(precision=0.10, total_executed=10))

        assert engine.state.min_profit_usd <= 25.0
        assert engine.state.min_roi <= 0.20

    def test_respects_lower_bounds(self):
        bounds = AdaptiveFilterBounds(
            min_profit_bounds=(5.0, 50.0),
            min_roi_bounds=(0.05, 0.40),
        )
        cfg = TruthEngineConfig(
            precision_target=0.70,
            min_samples_for_adaptation=5,
            adjustment_step=0.50,
        )
        engine = AdaptiveFilterEngine(config=cfg, bounds=bounds)

        # Relax many times
        for _ in range(20):
            engine.adapt(_metrics(precision=0.99, total_executed=10))

        assert engine.state.min_profit_usd >= 5.0
        assert engine.state.min_roi >= 0.05

    def test_asymmetric_relax_is_gentler_than_tighten(self):
        cfg = TruthEngineConfig(
            precision_target=0.70,
            min_samples_for_adaptation=5,
            adjustment_step=0.10,
        )
        engine_tighten = AdaptiveFilterEngine(config=cfg)
        engine_relax = AdaptiveFilterEngine(config=cfg)

        initial_profit = engine_tighten.state.min_profit_usd

        engine_tighten.adapt(_metrics(precision=0.30))
        engine_relax.adapt(_metrics(precision=0.95))

        tighten_delta = abs(engine_tighten.state.min_profit_usd - initial_profit)
        relax_delta = abs(engine_relax.state.min_profit_usd - initial_profit)

        assert tighten_delta > relax_delta

    def test_get_current_thresholds_dict(self):
        engine = AdaptiveFilterEngine()
        t = engine.get_current_thresholds()
        assert "min_profit_usd" in t
        assert "min_roi" in t
        assert "last_precision" in t
        assert "tighten_count" in t

    def test_multiple_adaptations_accumulate(self):
        cfg = TruthEngineConfig(precision_target=0.70, min_samples_for_adaptation=5)
        engine = AdaptiveFilterEngine(config=cfg)

        engine.adapt(_metrics(precision=0.40, total_executed=10))
        profit_after_1 = engine.state.min_profit_usd

        engine.adapt(_metrics(precision=0.40, total_executed=10))
        profit_after_2 = engine.state.min_profit_usd

        assert profit_after_2 > profit_after_1
        assert engine.state.tighten_count == 2
