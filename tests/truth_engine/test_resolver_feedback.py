"""
Tests — Resolver Feedback Store: penalty accumulation and confidence adjustment.
"""
import pytest

from truth_engine.config import TruthEngineConfig
from truth_engine.resolver_feedback import ResolverFeedbackStore


class TestResolverFeedbackStore:

    def test_record_failure_creates_penalty(self):
        store = ResolverFeedbackStore()
        penalty = store.record_failure("Samsung", "Galaxy S24", "mercadolibre_mx")
        assert penalty.penalty == 0.10
        assert penalty.failure_count == 1

    def test_penalties_accumulate(self):
        store = ResolverFeedbackStore()
        store.record_failure("Samsung", "Galaxy S24", "mercadolibre_mx")
        store.record_failure("Samsung", "Galaxy S24", "mercadolibre_mx")
        store.record_failure("Samsung", "Galaxy S24", "mercadolibre_mx")

        p = store.get_penalty("Samsung", "Galaxy S24", "mercadolibre_mx")
        assert p == pytest.approx(0.30, abs=0.01)

    def test_max_penalty_capped(self):
        cfg = TruthEngineConfig(mismatch_penalty=0.20, max_penalty_per_pair=0.50)
        store = ResolverFeedbackStore(config=cfg)

        for _ in range(10):
            store.record_failure("Apple", "iPhone 15", "ebay")

        p = store.get_penalty("Apple", "iPhone 15", "ebay")
        assert p == 0.50  # Capped at max

    def test_different_combos_independent(self):
        store = ResolverFeedbackStore()
        store.record_failure("Samsung", "Galaxy S24", "ebay")
        store.record_failure("Apple", "iPhone 15", "ebay")

        p1 = store.get_penalty("Samsung", "Galaxy S24", "ebay")
        p2 = store.get_penalty("Apple", "iPhone 15", "ebay")
        assert p1 == 0.10
        assert p2 == 0.10

    def test_no_penalty_returns_zero(self):
        store = ResolverFeedbackStore()
        assert store.get_penalty("Unknown", "Product", "nowhere") == 0.0

    def test_adjusted_confidence(self):
        store = ResolverFeedbackStore()
        store.record_failure("Sony", "WH1000XM4", "amazon")
        store.record_failure("Sony", "WH1000XM4", "amazon")

        adjusted = store.get_adjusted_confidence(
            "Sony", "WH1000XM4", "amazon", base_confidence=0.85
        )
        assert adjusted == pytest.approx(0.65, abs=0.01)

    def test_adjusted_confidence_floors_at_zero(self):
        cfg = TruthEngineConfig(mismatch_penalty=0.30, max_penalty_per_pair=1.0)
        store = ResolverFeedbackStore(config=cfg)

        for _ in range(5):
            store.record_failure("X", "Y", "Z")

        adjusted = store.get_adjusted_confidence("X", "Y", "Z", base_confidence=0.50)
        assert adjusted == 0.0

    def test_case_insensitive(self):
        store = ResolverFeedbackStore()
        store.record_failure("SAMSUNG", "Galaxy S24", "EBAY")

        p = store.get_penalty("samsung", "galaxy s24", "ebay")
        assert p == 0.10

    def test_get_all_penalties(self):
        store = ResolverFeedbackStore()
        store.record_failure("A", "B", "C")
        store.record_failure("X", "Y", "Z")

        all_p = store.get_all_penalties()
        assert len(all_p) == 2

    def test_clear(self):
        store = ResolverFeedbackStore()
        store.record_failure("A", "B", "C")
        store.clear()
        assert store.get_penalty("A", "B", "C") == 0.0
        assert len(store.get_all_penalties()) == 0
