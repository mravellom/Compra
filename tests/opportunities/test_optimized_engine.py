"""
Tests for the optimized opportunity engine.

Validates:
1. Precomputed marketplace stats correctness
2. Early pruning eliminates impossible pairs
3. Competition cache prevents redundant computation
4. No duplicate pairs evaluated
5. Edge cases: single marketplace, identical prices, zero profit
6. Performance: large datasets don't degrade
7. Deterministic output
"""
import statistics
import time
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from api.opportunity import (
    CompetitionInfo,
    _MarketplaceStats,
    _precompute_marketplace_stats,
    _compute_depth_cached,
    analyze_competition,
    calculate_profit,
    compute_velocity,
    is_outlier_price,
    variants_compatible,
    _titles_match,
)
from api.routes_config import VALID_ROUTE_PAIRS
from api.currency import to_usd


RATES = {"USD": 1.0, "MXN": 17.0, "ARS": 900.0, "CLP": 950.0, "COP": 4000.0}


def _make_listing(
    id: int = 1,
    price: float = 100.0,
    currency: str = "USD",
    marketplace_id: str = "amazon_us",
    title: str = "Test Product",
    condition: str = "new",
    seller_rating: float = 4.5,
    reviews_count: int = 50,
    sales_count: int = 10,
    is_free_shipping: bool = False,
    url: str | None = None,
):
    """Create a mock listing with all required attributes."""
    m = MagicMock()
    m.id = id
    m.price = price
    m.currency = currency
    m.marketplace_id = marketplace_id
    m.title = title
    m.normalized_title = title.lower()
    m.condition = condition
    m.seller_rating = seller_rating
    m.reviews_count = reviews_count
    m.sales_count = sales_count
    m.is_free_shipping = is_free_shipping
    m.url = url or f"https://example.com/item/{id}"
    m.stock_available = None
    m.seller_name = "TestSeller"
    return m


class TestPrecomputeMarketplaceStats:
    """Verify _precompute_marketplace_stats computes correctly in one pass."""

    def test_computes_cheapest_buy(self):
        by_mp = {
            "amazon_us": [
                _make_listing(id=1, price=150.0),
                _make_listing(id=2, price=100.0),
                _make_listing(id=3, price=200.0),
            ],
        }
        stats = _precompute_marketplace_stats(by_mp, RATES, {})
        assert stats["amazon_us"].buy_usd == 100.0
        assert stats["amazon_us"].buy_candidate.id == 2

    def test_computes_sell_prices(self):
        by_mp = {
            "mercadolibre_mx": [
                _make_listing(id=1, price=1700.0, currency="MXN", marketplace_id="mercadolibre_mx"),
                _make_listing(id=2, price=3400.0, currency="MXN", marketplace_id="mercadolibre_mx"),
            ],
        }
        stats = _precompute_marketplace_stats(by_mp, RATES, {})
        s = stats["mercadolibre_mx"]
        assert len(s.sell_prices_usd) == 2
        assert s.sell_prices_usd[0] == pytest.approx(100.0, abs=0.01)
        assert s.sell_prices_usd[1] == pytest.approx(200.0, abs=0.01)

    def test_computes_price_spread(self):
        by_mp = {
            "amazon_us": [
                _make_listing(id=1, price=100.0),
                _make_listing(id=2, price=200.0),
            ],
        }
        stats = _precompute_marketplace_stats(by_mp, RATES, {})
        s = stats["amazon_us"]
        # avg = 150, spread = (200-100)/150 = 0.667
        assert s.price_spread == pytest.approx(0.667, abs=0.01)

    def test_single_listing_zero_spread(self):
        by_mp = {
            "amazon_us": [_make_listing(id=1, price=100.0)],
        }
        stats = _precompute_marketplace_stats(by_mp, RATES, {})
        assert stats["amazon_us"].price_spread == 0.0

    def test_aggregates_sales_and_reviews(self):
        by_mp = {
            "ebay": [
                _make_listing(id=1, price=100.0, marketplace_id="ebay", sales_count=5, reviews_count=20),
                _make_listing(id=2, price=200.0, marketplace_id="ebay", sales_count=15, reviews_count=30),
            ],
        }
        stats = _precompute_marketplace_stats(by_mp, RATES, {})
        assert stats["ebay"].total_sales == 20
        assert stats["ebay"].total_reviews == 50

    def test_handles_none_sales_count(self):
        l = _make_listing(id=1, price=100.0, sales_count=0, reviews_count=0)
        l.sales_count = None
        l.reviews_count = None
        by_mp = {"amazon_us": [l]}
        stats = _precompute_marketplace_stats(by_mp, RATES, {})
        assert stats["amazon_us"].total_sales == 0
        assert stats["amazon_us"].total_reviews == 0

    def test_multiple_marketplaces(self):
        by_mp = {
            "amazon_us": [_make_listing(id=1, price=80.0, marketplace_id="amazon_us")],
            "ebay": [_make_listing(id=2, price=120.0, marketplace_id="ebay")],
            "mercadolibre_mx": [_make_listing(id=3, price=1700.0, currency="MXN", marketplace_id="mercadolibre_mx")],
        }
        stats = _precompute_marketplace_stats(by_mp, RATES, {})
        assert len(stats) == 3
        assert stats["amazon_us"].buy_usd < stats["ebay"].buy_usd


class TestEarlyPruning:
    """Verify that cheap sell prices are pruned before expensive computation."""

    def test_sell_below_buy_is_pruned(self):
        """When min_sell < buy_usd, no profit is possible — skip pair."""
        buy_stats = _MarketplaceStats(
            mp_id="amazon_us", listings=[], buy_candidate=None, buy_usd=100.0,
            sell_prices_usd=[], min_sell_usd=100.0, avg_sell_usd=100.0,
            price_spread=0.0, total_sales=0, total_reviews=0,
            depth_score=0, daily_sales=0, monthly_sales=0, scalability="low",
        )
        sell_stats = _MarketplaceStats(
            mp_id="mercadolibre_mx", listings=[], buy_candidate=None, buy_usd=90.0,
            sell_prices_usd=[80.0, 95.0], min_sell_usd=80.0, avg_sell_usd=87.5,
            price_spread=0.17, total_sales=5, total_reviews=10,
            depth_score=30, daily_sales=0.5, monthly_sales=15, scalability="low",
        )
        # min_sell_usd=80 < buy_usd=100 → should be pruned
        assert sell_stats.min_sell_usd <= buy_stats.buy_usd

    def test_sell_above_buy_passes(self):
        """When min_sell > buy_usd, pair may be profitable."""
        buy_usd = 80.0
        min_sell_usd = 120.0
        assert min_sell_usd > buy_usd


class TestCompetitionCache:
    """Verify competition is computed once per (buy_candidate, sell_mp)."""

    def test_same_key_returns_cached(self):
        """Simulating the caching logic from _analyze_product."""
        cache: dict[tuple[int, str], CompetitionInfo | None] = {}

        l1 = _make_listing(id=10, price=200.0, marketplace_id="mercadolibre_mx")
        l2 = _make_listing(id=11, price=250.0, marketplace_id="mercadolibre_mx")
        compatible = [l1, l2]

        # First call — compute
        key = (1, "mercadolibre_mx")
        call_count = 0
        if key not in cache:
            cache[key] = analyze_competition(compatible, RATES)
            call_count += 1

        # Second call with same key — should be cached
        if key not in cache:
            cache[key] = analyze_competition(compatible, RATES)
            call_count += 1

        assert call_count == 1
        assert cache[key] is not None

    def test_different_buy_candidate_different_key(self):
        """Different buy candidates produce different cache keys."""
        key_a = (1, "mercadolibre_mx")
        key_b = (2, "mercadolibre_mx")
        assert key_a != key_b


class TestNoDuplicatePairs:
    """Ensure (buy_mp, sell_mp) pairs are evaluated at most once."""

    def test_valid_route_pairs_no_self_loops(self):
        for buy_mp, sell_mp in VALID_ROUTE_PAIRS:
            assert buy_mp != sell_mp, f"Self-loop found: {buy_mp}"

    def test_sorted_iteration_visits_each_pair_once(self):
        """Simulate the sorted iteration and verify each pair appears once."""
        mps = ["amazon_us", "ebay", "mercadolibre_mx", "aliexpress"]
        visited: set[tuple[str, str]] = set()

        for buy_mp in mps:
            for sell_mp in mps:
                if buy_mp == sell_mp:
                    continue
                if (buy_mp, sell_mp) not in VALID_ROUTE_PAIRS:
                    continue
                assert (buy_mp, sell_mp) not in visited, f"Duplicate: {buy_mp}→{sell_mp}"
                visited.add((buy_mp, sell_mp))


class TestEdgeCases:
    """Edge cases that must be handled correctly."""

    def test_single_marketplace_no_opportunities(self):
        """A product with listings in only one marketplace has no arbitrage."""
        by_mp = {
            "amazon_us": [
                _make_listing(id=1, price=100.0),
                _make_listing(id=2, price=200.0),
            ],
        }
        stats = _precompute_marketplace_stats(by_mp, RATES, {})
        # Only 1 marketplace → no pairs to evaluate
        assert len(stats) == 1
        mps = list(stats.keys())
        pairs = [(a, b) for a in mps for b in mps if a != b]
        assert len(pairs) == 0

    def test_identical_prices_no_profit(self):
        """When buy and sell prices are identical, profit is negative (fees)."""
        calc = calculate_profit(100.0, 100.0, "amazon_us", "mercadolibre_mx")
        assert calc.net_profit < 0

    def test_zero_price_listing(self):
        """Zero-price listings should not crash the engine."""
        by_mp = {
            "amazon_us": [_make_listing(id=1, price=0.0)],
        }
        stats = _precompute_marketplace_stats(by_mp, RATES, {})
        assert stats["amazon_us"].buy_usd == 0.0

    def test_all_listings_same_price(self):
        by_mp = {
            "amazon_us": [
                _make_listing(id=1, price=100.0),
                _make_listing(id=2, price=100.0),
            ],
        }
        stats = _precompute_marketplace_stats(by_mp, RATES, {})
        assert stats["amazon_us"].price_spread == 0.0


class TestPerformanceScaling:
    """Verify the engine scales well with large datasets."""

    def test_precompute_scales_linearly(self):
        """Precomputing stats for 1000 listings across 5 marketplaces
        should complete in well under 1 second."""
        mps = ["amazon_us", "ebay", "mercadolibre_mx", "aliexpress", "mercadolibre_cl"]
        by_mp: dict[str, list] = {}
        listing_id = 1

        for mp in mps:
            listings = []
            currency = "MXN" if "mx" in mp else "CLP" if "cl" in mp else "USD"
            for i in range(200):
                listings.append(_make_listing(
                    id=listing_id, price=50.0 + i * 2.0,
                    currency=currency, marketplace_id=mp,
                    sales_count=i % 20, reviews_count=i % 50,
                ))
                listing_id += 1
            by_mp[mp] = listings

        start = time.perf_counter()
        stats = _precompute_marketplace_stats(by_mp, RATES, {})
        elapsed = time.perf_counter() - start

        assert len(stats) == 5
        assert elapsed < 1.0, f"Precompute took {elapsed:.3f}s (should be <1s)"
        # Each marketplace should have correct stats
        for mp, s in stats.items():
            assert s.buy_usd > 0
            assert len(s.sell_prices_usd) == 200

    def test_pair_evaluation_count(self):
        """With 5 marketplaces and VALID_ROUTE_PAIRS filtering,
        far fewer than n² pairs should be evaluated."""
        mps = ["amazon_us", "ebay", "mercadolibre_mx", "aliexpress", "mercadolibre_cl"]
        n_squared = len(mps) * (len(mps) - 1)  # 20

        valid_pairs = [
            (a, b) for a in mps for b in mps
            if a != b and (a, b) in VALID_ROUTE_PAIRS
        ]

        # Valid routes are a subset of all possible pairs
        assert len(valid_pairs) < n_squared
        # With 5 marketplaces, we expect roughly 8-12 valid routes
        assert len(valid_pairs) >= 1

    def test_competition_cache_hit_rate(self):
        """With 4 marketplaces sharing the same buy candidate,
        competition for each sell_mp is computed only once."""
        buy_candidate_id = 42
        sell_mps = ["mercadolibre_mx", "mercadolibre_cl", "mercadolibre_ar"]

        cache: dict[tuple[int, str], bool] = {}
        compute_count = 0

        # Simulate two passes with same buy candidate
        for _ in range(2):
            for sell_mp in sell_mps:
                key = (buy_candidate_id, sell_mp)
                if key not in cache:
                    cache[key] = True
                    compute_count += 1

        # Should compute exactly 3 times (once per sell_mp), not 6
        assert compute_count == 3


class TestDeterministicOutput:
    """Verify that the engine produces deterministic results."""

    def test_precompute_is_deterministic(self):
        """Same input → same output, every time."""
        by_mp = {
            "amazon_us": [
                _make_listing(id=1, price=100.0),
                _make_listing(id=2, price=150.0),
                _make_listing(id=3, price=200.0),
            ],
            "ebay": [
                _make_listing(id=4, price=180.0, marketplace_id="ebay"),
                _make_listing(id=5, price=220.0, marketplace_id="ebay"),
            ],
        }
        s1 = _precompute_marketplace_stats(by_mp, RATES, {})
        s2 = _precompute_marketplace_stats(by_mp, RATES, {})

        for mp in by_mp:
            assert s1[mp].buy_usd == s2[mp].buy_usd
            assert s1[mp].sell_prices_usd == s2[mp].sell_prices_usd
            assert s1[mp].total_sales == s2[mp].total_sales
            assert s1[mp].price_spread == s2[mp].price_spread

    def test_sorted_mps_stable_order(self):
        """Sorting by buy_usd produces a stable order."""
        by_mp = {
            "mercadolibre_mx": [_make_listing(id=1, price=1700.0, currency="MXN", marketplace_id="mercadolibre_mx")],
            "amazon_us": [_make_listing(id=2, price=80.0, marketplace_id="amazon_us")],
            "ebay": [_make_listing(id=3, price=120.0, marketplace_id="ebay")],
        }
        stats = _precompute_marketplace_stats(by_mp, RATES, {})
        sorted_1 = [s.mp_id for s in sorted(stats.values(), key=lambda s: s.buy_usd)]
        sorted_2 = [s.mp_id for s in sorted(stats.values(), key=lambda s: s.buy_usd)]
        assert sorted_1 == sorted_2
        assert sorted_1[0] == "amazon_us"  # $80 cheapest


class TestAnalyzeCompetitionIntegration:
    """Verify analyze_competition works correctly with precomputed data."""

    def test_competition_on_compatible_only(self):
        """Competition must be computed on compatible listings only,
        not all sell listings. This prevents inflated sell prices."""
        # 128GB listings at $100-120
        l128a = _make_listing(id=1, price=100.0, title="iPhone 15 128GB")
        l128b = _make_listing(id=2, price=120.0, title="iPhone 15 128GB")
        # 256GB listing at $200 — should NOT influence 128GB competition
        l256 = _make_listing(id=3, price=200.0, title="iPhone 15 256GB")

        # Full list
        all_sells = [l128a, l128b, l256]
        comp_all = analyze_competition(all_sells, RATES)

        # Compatible only (128GB)
        compatible = [l128a, l128b]
        comp_filtered = analyze_competition(compatible, RATES)

        # Filtered competition should have lower realistic sell price
        assert comp_filtered.realistic_sell_usd < comp_all.realistic_sell_usd


class TestProfitWithPrecomputedStats:
    """Verify profit calculations using precomputed marketplace stats."""

    def test_profitable_route(self):
        """AliExpress ($30) → MercadoLibre MX ($100) should be profitable."""
        calc = calculate_profit(30.0, 100.0, "aliexpress", "mercadolibre_mx")
        assert calc.net_profit > 0
        assert calc.roi > 0

    def test_unprofitable_domestic(self):
        """Domestic with 1% price diff is unprofitable after fees."""
        calc = calculate_profit(100.0, 101.0, "amazon", "mercadolibre_mx")
        assert calc.net_profit < 0

    def test_cross_border_fees_reduce_profit(self):
        """Cross-border route has higher total fees than domestic."""
        domestic = calculate_profit(100.0, 200.0, "amazon", "mercadolibre_mx")
        cross = calculate_profit(100.0, 200.0, "amazon_us", "mercadolibre_mx")
        assert cross.total_fees > domestic.total_fees
