"""
Validation Tests — Opportunity Engine (Price Calculations)

Tests the production calculate_profit() with marketplace-specific fees.
Also tests IQR outlier detection and cross-border fee logic.
"""
import pytest

from api.opportunity import (
    ProfitCalc,
    calculate_profit,
    is_outlier_price,
    variants_compatible,
)
from api.routes_config import (
    is_cross_border,
    MARKETPLACE_FEES,
)


class TestCalculateProfit:
    """Tests for production calculate_profit with marketplace fees."""

    def test_domestic_amazon_to_ml_mx(self):
        """Same country (MX): no cross-border fees, but VAT applies."""
        result = calculate_profit(100.0, 200.0, "amazon", "mercadolibre_mx")
        assert not is_cross_border("amazon", "mercadolibre_mx")
        # ML MX: 16% commission + 3.6% payment + $0.30 fixed + 16% IVA
        assert result.marketplace_fee == pytest.approx(32.0, abs=0.01)
        assert result.payment_fee == pytest.approx(7.50, abs=0.01)  # 200*0.036 + 0.30
        assert result.sell_tax == pytest.approx(32.0, abs=0.01)  # 200*0.16 IVA
        assert result.import_tax == 0.0
        assert result.international_shipping == 0.0

    def test_cross_border_mx_to_ar(self):
        """MX -> AR: 50% import tax on CIF (buy + insurance + shipping) + $14 shipping."""
        result = calculate_profit(100.0, 300.0, "amazon", "mercadolibre_ar")
        assert is_cross_border("amazon", "mercadolibre_ar")
        # CIF = 100 + 2.0 (2% insurance) + 14 = 116, import_tax = 116 * 0.50 = 58.0
        assert result.import_tax == pytest.approx(58.0, abs=0.01)
        assert result.international_shipping == 14.0

    def test_cross_border_ar_to_mx(self):
        """AR -> MX: 16% import tax on CIF (buy + insurance + shipping) + $45 shipping."""
        result = calculate_profit(100.0, 300.0, "mercadolibre_ar", "mercadolibre_mx")
        # CIF = 100 + 2.0 (2% insurance) + 45 = 147, import_tax = 147 * 0.16 = 23.52
        assert result.import_tax == pytest.approx(23.52, abs=0.01)
        assert result.international_shipping == 45.0

    def test_cross_border_us_to_mx(self):
        """US -> MX: 16% import tax + $15 shipping."""
        result = calculate_profit(100.0, 300.0, "amazon_us", "mercadolibre_mx")
        assert is_cross_border("amazon_us", "mercadolibre_mx")
        # CIF = 100 + 2.0 + 15 = 117, import_tax = 117 * 0.16 = 18.72
        assert result.import_tax == pytest.approx(18.72, abs=0.01)
        assert result.international_shipping == 15.0

    def test_cross_border_cn_to_mx(self):
        """CN -> MX: 16% import tax + $8 shipping (AliExpress)."""
        result = calculate_profit(50.0, 200.0, "aliexpress", "mercadolibre_mx")
        assert is_cross_border("aliexpress", "mercadolibre_mx")
        # CIF = 50 + 1.0 + 8 = 59, import_tax = 59 * 0.16 = 9.44
        assert result.import_tax == pytest.approx(9.44, abs=0.01)
        assert result.international_shipping == 8.0

    def test_payment_fee_only_when_payment_processing(self):
        """Amazon has 0% payment_processing, should NOT add $0.30 fixed fee."""
        result = calculate_profit(100.0, 200.0, "mercadolibre_mx", "amazon")
        # Amazon: 15% commission, 0% payment, no $0.30
        assert result.payment_fee == 0.0

    def test_payment_fee_with_ml(self):
        """ML has 3.6% payment_processing + $0.30 fixed fee."""
        result = calculate_profit(100.0, 200.0, "amazon", "mercadolibre_mx")
        # 200 * 0.036 + 0.30 = 7.50
        assert result.payment_fee == pytest.approx(7.50, abs=0.01)

    def test_zero_buy_price_no_division_error(self):
        """buy_price=0 → ROI must be 0.0, no ZeroDivisionError."""
        result = calculate_profit(0.0, 100.0, "amazon", "mercadolibre_mx")
        assert result.roi == 0.0

    def test_roi_is_net_profit_over_buy_price(self):
        result = calculate_profit(100.0, 200.0, "amazon", "mercadolibre_mx")
        expected_roi = result.net_profit / result.buy_price_usd
        assert result.roi == pytest.approx(expected_roi, abs=0.0001)

    def test_margin_is_net_profit_over_sell_price(self):
        result = calculate_profit(100.0, 200.0, "amazon", "mercadolibre_mx")
        expected_margin = result.net_profit / result.sell_price_usd
        assert result.margin == pytest.approx(expected_margin, abs=0.0001)

    def test_sell_below_buy_negative_roi(self):
        result = calculate_profit(200.0, 100.0, "amazon", "mercadolibre_mx")
        assert result.net_profit < 0
        assert result.roi < 0

    def test_return_type_is_profit_calc(self):
        assert isinstance(calculate_profit(100.0, 200.0, "amazon", "ebay"), ProfitCalc)

    def test_free_shipping_overrides_domestic(self):
        """sell_free_ship=True should zero out domestic shipping."""
        # ebay has $8 domestic shipping default
        result_no_free = calculate_profit(100.0, 200.0, "amazon", "ebay", sell_free_ship=False)
        result_free = calculate_profit(100.0, 200.0, "amazon", "ebay", sell_free_ship=True)
        assert result_free.domestic_shipping == 0.0
        assert result_no_free.domestic_shipping == 8.0
        assert result_free.net_profit > result_no_free.net_profit


class TestVariantsCompetitionIntegration:
    """Verify that competition is computed on compatible listings only."""

    def test_analyze_competition_basic(self):
        """analyze_competition returns correct median-based realistic price."""
        from unittest.mock import MagicMock
        from api.opportunity import analyze_competition

        rates = {"USD": 1.0, "MXN": 17.0}

        # Two listings at $100 and $200
        l1 = MagicMock(price=100.0, currency="USD")
        l2 = MagicMock(price=200.0, currency="USD")
        comp = analyze_competition([l1, l2], rates)
        # median([100, 200]) = 150, realistic = 150 * 0.97 = 145.5
        assert comp.realistic_sell_usd == pytest.approx(145.5, abs=0.01)

    def test_compatible_filter_excludes_mismatched_variants(self):
        """Incompatible storage variants should be excluded."""
        from unittest.mock import MagicMock

        buy = MagicMock(title="iPhone 15 128gb Negro", condition="new")
        sell_128 = MagicMock(title="iPhone 15 128gb Azul", condition="new")
        sell_256 = MagicMock(title="iPhone 15 256gb Negro", condition="new")

        assert variants_compatible(buy, sell_128) is True
        assert variants_compatible(buy, sell_256) is False

    def test_condition_mismatch_blocks_arbitrage(self):
        """Used/refurbished vs new must be rejected — prevents phantom profits."""
        from unittest.mock import MagicMock

        buy_used = MagicMock(title="iPhone 15 128gb", condition="used")
        sell_new = MagicMock(title="iPhone 15 128gb", condition="new")
        sell_refurb = MagicMock(title="iPhone 15 128gb", condition="refurbished")
        sell_used = MagicMock(title="iPhone 15 128gb", condition="used")

        assert variants_compatible(buy_used, sell_new) is False
        assert variants_compatible(buy_used, sell_refurb) is False
        assert variants_compatible(buy_used, sell_used) is True

    def test_vat_included_in_total_fees(self):
        """VAT/IVA must be part of total_fees and reduce net_profit."""
        result_mx = calculate_profit(100.0, 200.0, "mercadolibre_mx", "mercadolibre_mx")
        # sell_tax = 200 * 0.16 = 32.0
        assert result_mx.sell_tax == pytest.approx(32.0, abs=0.01)
        assert result_mx.sell_tax > 0
        # total_fees must include sell_tax
        expected_total = (
            result_mx.marketplace_fee + result_mx.payment_fee + result_mx.sell_tax
            + result_mx.import_tax + result_mx.domestic_shipping + result_mx.international_shipping
        )
        assert result_mx.total_fees == pytest.approx(expected_total, abs=0.01)

    def test_ebay_no_vat(self):
        """eBay (US) should have zero VAT."""
        result = calculate_profit(100.0, 200.0, "amazon_us", "ebay")
        assert result.sell_tax == 0.0


class TestOutlierDetection:

    def test_too_few_prices_no_outlier(self):
        assert is_outlier_price(1000.0, [10.0, 20.0, 30.0]) is False

    def test_normal_price_not_outlier(self):
        prices = [100.0, 105.0, 110.0, 95.0, 102.0]
        assert is_outlier_price(108.0, prices) is False

    def test_extreme_high_is_outlier(self):
        prices = [100.0, 105.0, 110.0, 95.0, 102.0]
        assert is_outlier_price(500.0, prices) is True

    def test_extreme_low_is_outlier(self):
        prices = [100.0, 105.0, 110.0, 95.0, 102.0]
        assert is_outlier_price(1.0, prices) is True

    def test_uses_proper_quartiles(self):
        """Verify IQR uses statistics.quantiles with 1.5x multiplier."""
        prices = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0]
        # Q1=22.5, Q3=67.5, IQR=45, lower=-45.0, upper=135.0
        assert is_outlier_price(150.0, prices) is True   # above upper bound
        assert is_outlier_price(80.0, prices) is False    # within range
        assert is_outlier_price(5.0, prices) is False     # within range (lower=-45)


class TestCrossBorder:

    def test_same_country_not_cross_border(self):
        assert not is_cross_border("amazon", "mercadolibre_mx")

    def test_different_country_is_cross_border(self):
        assert is_cross_border("amazon", "mercadolibre_ar")
        assert is_cross_border("mercadolibre_ar", "amazon")
        assert is_cross_border("amazon_us", "mercadolibre_mx")

    def test_us_marketplaces_same_country(self):
        assert not is_cross_border("amazon_us", "ebay")

    def test_cn_is_cross_border(self):
        assert is_cross_border("aliexpress", "mercadolibre_mx")
        assert is_cross_border("aliexpress", "amazon")

    def test_cl_cross_border(self):
        assert is_cross_border("amazon_us", "mercadolibre_cl")
        assert is_cross_border("amazon", "mercadolibre_cl")


class TestRouteConfig:

    def test_all_marketplaces_have_required_keys(self):
        required = {"commission", "payment_processing", "vat_rate", "domestic_shipping", "currency", "country"}
        for mp_id, fees in MARKETPLACE_FEES.items():
            missing = required - set(fees.keys())
            assert not missing, f"{mp_id} missing keys: {missing}"

    def test_route_labels(self):
        from api.routes_config import get_route_label
        assert get_route_label("amazon_us", "mercadolibre_mx") == "US→MX"
        assert get_route_label("aliexpress", "mercadolibre_mx") == "CN→MX"
        assert get_route_label("amazon", "mercadolibre_mx") == "MX→MX"

    def test_valid_routes_exist(self):
        from api.routes_config import VALID_ROUTE_PAIRS
        assert ("amazon_us", "mercadolibre_mx") in VALID_ROUTE_PAIRS
        assert ("aliexpress", "mercadolibre_mx") in VALID_ROUTE_PAIRS
        assert ("amazon", "mercadolibre_mx") in VALID_ROUTE_PAIRS
        # Invalid routes should not exist
        assert ("mercadolibre_mx", "mercadolibre_mx") not in VALID_ROUTE_PAIRS

    def test_route_difficulty(self):
        from api.routes_config import get_route_difficulty
        assert get_route_difficulty("amazon", "mercadolibre_mx") == 1  # domestic
        assert get_route_difficulty("amazon_us", "mercadolibre_cl") == 2  # medium
        assert get_route_difficulty("amazon_us", "mercadolibre_ar") == 3  # hard
