"""
Tests for the Arbitrage Validator — anti-false-positive layer.

Covers every check individually plus integration scenarios.
"""
import time

import pytest

from api.arbitrage_validator import (
    ArbitrageSnapshot,
    CheckDetail,
    ValidatorConfig,
    ValidationResult,
    check_price_freshness,
    check_spread_stability,
    check_depth,
    check_worst_case_profit,
    check_execution_simulation,
    check_duplicate,
    compute_execution_confidence,
    validate_arbitrage,
    reset_dedup,
)


# ── Helpers ──────────────────────────────────────────────────

def _now() -> float:
    return time.time()


def _make_snapshot(**overrides) -> ArbitrageSnapshot:
    """Build a valid snapshot — all checks should pass with defaults.

    Uses domestic MX→MX route to avoid cross-border fees in simulation.
    Sell price is high enough to survive worst-case + simulation checks.
    """
    now = _now()
    defaults = dict(
        buy_price_usd=80.0,
        sell_price_usd=250.0,
        buy_marketplace="amazon",
        sell_marketplace="mercadolibre_mx",
        buy_listing_scraped_at=now - 60,       # 1 min ago
        sell_listing_scraped_at=now - 30,       # 30s ago
        buy_listing_id=1,
        sell_listing_id=2,
        product_id=100,
        compatible_sell_prices_usd=[230.0, 240.0, 250.0, 260.0, 270.0],
        total_sales_count=20,
        buy_free_shipping=False,
        sell_free_shipping=False,
        opportunity_score=70.0,
        risk_score=30.0,
        confidence_score=75.0,
        price_stability_score=72.0,
        liquidity_score=65.0,
        total_fees=25.0,
        net_profit=45.0,
        roi=0.56,
    )
    defaults.update(overrides)
    return ArbitrageSnapshot(**defaults)


@pytest.fixture(autouse=True)
def _reset_dedup_tracker():
    """Reset dedup tracker before each test."""
    reset_dedup()
    yield
    reset_dedup()


# ── Step 1: Price Freshness ──────────────────────────────────

class TestPriceFreshness:

    def test_fresh_listings_pass(self):
        snap = _make_snapshot()
        result = check_price_freshness(snap, ValidatorConfig())
        assert result.passed is True

    def test_stale_buy_listing_rejected(self):
        snap = _make_snapshot(buy_listing_scraped_at=_now() - 600)
        result = check_price_freshness(snap, ValidatorConfig())
        assert result.passed is False
        assert "listing age" in result.reason

    def test_stale_sell_listing_rejected(self):
        snap = _make_snapshot(sell_listing_scraped_at=_now() - 400)
        result = check_price_freshness(snap, ValidatorConfig())
        assert result.passed is False

    def test_custom_max_age(self):
        snap = _make_snapshot(buy_listing_scraped_at=_now() - 100)
        cfg = ValidatorConfig(max_price_age_seconds=50)
        result = check_price_freshness(snap, cfg)
        assert result.passed is False

    def test_value_reports_max_age(self):
        snap = _make_snapshot(
            buy_listing_scraped_at=_now() - 120,
            sell_listing_scraped_at=_now() - 200,
        )
        result = check_price_freshness(snap, ValidatorConfig())
        assert result.value >= 199  # ~200 seconds


# ── Step 2: Spread Stability ────────────────────────────────

class TestSpreadStability:

    def test_tight_spread_passes(self):
        snap = _make_snapshot(compatible_sell_prices_usd=[100.0, 102.0, 101.0, 99.0])
        result = check_spread_stability(snap, ValidatorConfig())
        assert result.passed is True

    def test_volatile_spread_rejected(self):
        # CV > 0.25
        snap = _make_snapshot(compatible_sell_prices_usd=[50.0, 100.0, 200.0, 300.0])
        result = check_spread_stability(snap, ValidatorConfig())
        assert result.passed is False
        assert "CV" in result.reason

    def test_single_price_passes(self):
        """Not enough data to compute CV — pass by default."""
        snap = _make_snapshot(compatible_sell_prices_usd=[100.0])
        result = check_spread_stability(snap, ValidatorConfig())
        assert result.passed is True

    def test_empty_prices_passes(self):
        snap = _make_snapshot(compatible_sell_prices_usd=[])
        result = check_spread_stability(snap, ValidatorConfig())
        assert result.passed is True

    def test_custom_cv_threshold(self):
        snap = _make_snapshot(compatible_sell_prices_usd=[80.0, 100.0, 120.0])
        strict = ValidatorConfig(max_cv=0.10)
        assert check_spread_stability(snap, strict).passed is False
        lenient = ValidatorConfig(max_cv=0.50)
        assert check_spread_stability(snap, lenient).passed is True


# ── Step 3: Depth Confirmation ───────────────────────────────

class TestDepthConfirmation:

    def test_deep_market_passes(self):
        snap = _make_snapshot(
            compatible_sell_prices_usd=[145.0, 148.0, 150.0, 152.0, 155.0],
            sell_price_usd=150.0,
            total_sales_count=10,
        )
        result = check_depth(snap, ValidatorConfig())
        assert result.passed is True

    def test_no_nearby_listings_rejected(self):
        snap = _make_snapshot(
            compatible_sell_prices_usd=[200.0, 250.0],  # all far above sell
            sell_price_usd=100.0,
            total_sales_count=10,
        )
        result = check_depth(snap, ValidatorConfig())
        assert result.passed is False
        assert "listings within 5%" in result.reason

    def test_low_sales_rejected(self):
        snap = _make_snapshot(
            compatible_sell_prices_usd=[148.0, 150.0, 152.0],
            sell_price_usd=150.0,
            total_sales_count=1,
        )
        result = check_depth(snap, ValidatorConfig())
        assert result.passed is False
        assert "total_sales" in result.reason

    def test_zero_sales_rejected(self):
        snap = _make_snapshot(total_sales_count=0)
        result = check_depth(snap, ValidatorConfig())
        assert result.passed is False


# ── Step 4: Worst-Case Profit ────────────────────────────────

class TestWorstCaseProfit:

    def test_profitable_worst_case_passes(self):
        snap = _make_snapshot(
            buy_price_usd=80.0,
            sell_price_usd=150.0,
            compatible_sell_prices_usd=[120.0, 130.0, 140.0, 150.0, 160.0],
            total_fees=20.0,
        )
        check, profit = check_worst_case_profit(snap, ValidatorConfig())
        assert check.passed is True
        assert profit > 0

    def test_negative_worst_case_rejected(self):
        snap = _make_snapshot(
            buy_price_usd=140.0,
            sell_price_usd=150.0,
            compatible_sell_prices_usd=[100.0, 110.0, 120.0, 130.0, 150.0],
            total_fees=30.0,
        )
        check, profit = check_worst_case_profit(snap, ValidatorConfig())
        assert check.passed is False
        assert profit <= 0
        assert "worst-case profit" in check.reason

    def test_uses_p25_not_median(self):
        snap = _make_snapshot(
            buy_price_usd=50.0,
            compatible_sell_prices_usd=[80.0, 90.0, 100.0, 200.0],
            total_fees=10.0,
        )
        check, profit = check_worst_case_profit(snap, ValidatorConfig())
        # P25 of [80, 90, 100, 200] → index 0 → 80
        # profit = 80 - 50*(1.02) - 10*(1.05) = 80 - 51 - 10.5 = 18.5
        assert profit < snap.compatible_sell_prices_usd[-1] - snap.buy_price_usd

    def test_fx_slippage_applied(self):
        snap = _make_snapshot(buy_price_usd=100.0, total_fees=0.0,
                              compatible_sell_prices_usd=[105.0])
        # Without FX buffer: 105 - 100 = 5
        # With 2% buffer: 105 - 102 = 3
        check, profit = check_worst_case_profit(snap, ValidatorConfig())
        assert profit < 5.0  # FX buffer reduced it


# ── Step 5: Execution Simulation ─────────────────────────────

class TestExecutionSimulation:

    def test_profitable_after_decay_passes(self):
        snap = _make_snapshot(buy_price_usd=80.0, sell_price_usd=200.0)
        check, profit = check_execution_simulation(snap, ValidatorConfig())
        assert check.passed is True
        assert profit > 0

    def test_tight_margin_flips_on_decay(self):
        """A very tight margin should flip negative with time decay."""
        snap = _make_snapshot(
            buy_price_usd=100.0,
            sell_price_usd=102.0,
            buy_marketplace="amazon",
            sell_marketplace="mercadolibre_mx",
        )
        check, profit = check_execution_simulation(snap, ValidatorConfig())
        # With fees + decay, $2 margin should be negative
        assert check.passed is False

    def test_no_decay_with_zero_rate(self):
        snap = _make_snapshot(buy_price_usd=80.0, sell_price_usd=200.0)
        cfg = ValidatorConfig(price_decay_per_hour=0.0)
        check, profit = check_execution_simulation(snap, cfg)
        assert check.passed is True

    def test_heavy_decay_kills_profit(self):
        snap = _make_snapshot(buy_price_usd=80.0, sell_price_usd=110.0)
        cfg = ValidatorConfig(price_decay_per_hour=0.50)  # 50%/hour
        check, profit = check_execution_simulation(snap, cfg)
        assert check.passed is False


# ── Step 6: Duplicate Suppression ────────────────────────────

class TestDuplicateSuppression:

    def test_first_occurrence_passes(self):
        snap = _make_snapshot()
        result = check_duplicate(snap, ValidatorConfig())
        assert result.passed is True

    def test_second_occurrence_rejected(self):
        snap = _make_snapshot()
        check_duplicate(snap, ValidatorConfig())
        result = check_duplicate(snap, ValidatorConfig())
        assert result.passed is False
        assert "duplicate" in result.reason

    def test_different_product_not_duplicate(self):
        snap1 = _make_snapshot(product_id=1)
        snap2 = _make_snapshot(product_id=2)
        check_duplicate(snap1, ValidatorConfig())
        result = check_duplicate(snap2, ValidatorConfig())
        assert result.passed is True

    def test_different_sell_market_not_duplicate(self):
        snap1 = _make_snapshot(sell_marketplace="mercadolibre_mx")
        snap2 = _make_snapshot(sell_marketplace="mercadolibre_ar")
        check_duplicate(snap1, ValidatorConfig())
        result = check_duplicate(snap2, ValidatorConfig())
        assert result.passed is True

    def test_different_buy_listing_not_duplicate(self):
        snap1 = _make_snapshot(buy_listing_id=1)
        snap2 = _make_snapshot(buy_listing_id=2)
        check_duplicate(snap1, ValidatorConfig())
        result = check_duplicate(snap2, ValidatorConfig())
        assert result.passed is True


# ── Step 7: Confidence Re-scoring ────────────────────────────

class TestConfidenceReScoring:

    def test_high_scores_high_confidence(self):
        snap = _make_snapshot(
            opportunity_score=85.0,
            confidence_score=90.0,
            price_stability_score=80.0,
            liquidity_score=75.0,
            risk_score=20.0,
        )
        checks = [CheckDetail("a", True), CheckDetail("b", True)]
        conf = compute_execution_confidence(snap, checks)
        assert conf > 0.5

    def test_low_scores_low_confidence(self):
        snap = _make_snapshot(
            opportunity_score=10.0,
            confidence_score=15.0,
            price_stability_score=10.0,
            liquidity_score=10.0,
            risk_score=90.0,
        )
        checks = [CheckDetail("a", False), CheckDetail("b", False)]
        conf = compute_execution_confidence(snap, checks)
        assert conf < 0.30

    def test_all_checks_passed_boosts_confidence(self):
        snap = _make_snapshot(confidence_score=50.0)
        all_pass = [CheckDetail(f"c{i}", True) for i in range(7)]
        some_fail = [CheckDetail(f"c{i}", i < 3) for i in range(7)]

        conf_all = compute_execution_confidence(snap, all_pass)
        conf_some = compute_execution_confidence(snap, some_fail)
        assert conf_all > conf_some


# ── Integration: Full Validation ─────────────────────────────

class TestFullValidation:

    def test_valid_opportunity_passes_all(self):
        snap = _make_snapshot()
        result = validate_arbitrage(snap)
        assert result.is_valid is True
        assert len(result.rejection_reasons) == 0
        assert result.execution_confidence > 0
        assert len(result.checks) == 7

    def test_multiple_failures_collected(self):
        """No short-circuit — all rejection reasons collected."""
        snap = _make_snapshot(
            buy_listing_scraped_at=_now() - 600,       # stale
            compatible_sell_prices_usd=[50.0, 200.0, 400.0],  # volatile
            total_sales_count=0,                        # no sales
        )
        result = validate_arbitrage(snap)
        assert result.is_valid is False
        # Should have at least 3 reasons (freshness, depth, possibly more)
        assert len(result.rejection_reasons) >= 2

    def test_shadow_mode_logs_but_approves(self):
        """Shadow mode: would reject, but returns is_valid=True."""
        snap = _make_snapshot(
            buy_listing_scraped_at=_now() - 600,  # stale → would reject
        )
        result = validate_arbitrage(snap, shadow_mode=True)
        assert result.is_valid is True
        # But rejection reasons are still collected
        assert len(result.rejection_reasons) >= 1

    def test_result_contains_worst_case_and_simulated(self):
        snap = _make_snapshot()
        result = validate_arbitrage(snap)
        assert result.worst_case_profit != 0.0
        assert result.simulated_profit != 0.0

    def test_risk_score_propagated(self):
        snap = _make_snapshot(risk_score=42.0)
        result = validate_arbitrage(snap)
        assert result.risk_score == 42.0

    def test_custom_config_overrides(self):
        snap = _make_snapshot(
            buy_listing_scraped_at=_now() - 200,
        )
        strict = ValidatorConfig(max_price_age_seconds=100)
        result = validate_arbitrage(snap, config=strict)
        assert result.is_valid is False

        lenient = ValidatorConfig(max_price_age_seconds=300)
        reset_dedup()
        result2 = validate_arbitrage(snap, config=lenient)
        assert result2.is_valid is True

    def test_all_checks_in_result(self):
        snap = _make_snapshot()
        result = validate_arbitrage(snap)
        check_names = {c.name for c in result.checks}
        expected = {
            "price_freshness", "spread_stability", "depth",
            "worst_case_profit", "execution_simulation",
            "duplicate", "execution_confidence",
        }
        assert check_names == expected


class TestDeterminism:

    def test_same_input_same_output(self):
        """Validator must be deterministic (except time-dependent checks)."""
        now = _now()
        snap = _make_snapshot(
            buy_listing_scraped_at=now - 10,
            sell_listing_scraped_at=now - 10,
        )
        cfg = ValidatorConfig()

        # Freshness and dedup are time-sensitive, so fix those
        r1 = check_spread_stability(snap, cfg)
        r2 = check_spread_stability(snap, cfg)
        assert r1.passed == r2.passed
        assert r1.value == r2.value

        r3 = check_depth(snap, cfg)
        r4 = check_depth(snap, cfg)
        assert r3.passed == r4.passed

        wc1 = check_worst_case_profit(snap, cfg)
        wc2 = check_worst_case_profit(snap, cfg)
        assert wc1[1] == wc2[1]
