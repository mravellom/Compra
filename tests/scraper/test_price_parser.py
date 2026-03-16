"""
Unit Tests — Price Parser (multi-marketplace, multi-currency).

Tests:
  - Amazon MXN parsing
  - MercadoLibre ARS/MXN/CLP/COP parsing
  - eBay USD/EUR/GBP parsing
  - Price range handling
  - Ambiguous number disambiguation
  - Price validation bounds
  - HTML element extractors
"""
import pytest
from bs4 import BeautifulSoup

from scraper.price_parser import (
    normalize_price,
    validate_price,
    extract_amazon_price,
    extract_ebay_price,
    extract_ml_price,
)


# ═══════════════════════════════════════════════════════════════
# 1. normalize_price — core parsing
# ═══════════════════════════════════════════════════════════════


class TestNormalizePriceAmazon:

    def test_standard_mxn(self):
        assert normalize_price("$5,199.00", "amazon") == 5199.00

    def test_no_cents(self):
        assert normalize_price("$18,999", "amazon") == 18999.0

    def test_with_currency_symbol(self):
        assert normalize_price("MXN$1,299.00", "amazon") == 1299.00

    def test_low_price(self):
        assert normalize_price("$99.00", "amazon") == 99.00

    def test_empty_string(self):
        assert normalize_price("", "amazon") is None

    def test_non_numeric(self):
        assert normalize_price("No disponible", "amazon") is None


class TestNormalizePriceMercadoLibre:

    def test_ars_dot_as_thousands(self):
        """ARS: '405.188' = 405188 (dot is thousands separator)."""
        result = normalize_price("405.188", "mercadolibre_ar", "ARS")
        assert result == 405188.0

    def test_mxn_comma_as_thousands(self):
        """MXN: '5,299' = 5299."""
        result = normalize_price("5,299", "mercadolibre_mx", "MXN")
        assert result == 5299.0

    def test_mxn_with_cents(self):
        result = normalize_price("5,299.99", "mercadolibre_mx", "MXN")
        assert result == 5299.99

    def test_clp_no_decimals(self):
        """CLP: '89.990' = 89990 (no decimals)."""
        result = normalize_price("89.990", "mercadolibre_cl", "CLP")
        assert result == 89990.0

    def test_cop_large_number(self):
        result = normalize_price("2.500.000", "mercadolibre_co", "COP")
        assert result == 2500000.0

    def test_ars_with_comma_cents(self):
        """ARS: '193,70' = 193.70 (comma is decimal, 2 digits after)."""
        result = normalize_price("193,70", "mercadolibre_ar", "ARS")
        assert result == 193.70


class TestNormalizePriceEbay:

    def test_standard_usd(self):
        assert normalize_price("$229.99", "ebay") == 229.99

    def test_us_dollar_prefix(self):
        assert normalize_price("US $29.99", "ebay") == 29.99

    def test_eur(self):
        assert normalize_price("EUR 89.99", "ebay") == 89.99

    def test_gbp(self):
        assert normalize_price("£59.99", "ebay") == 59.99

    def test_thousands(self):
        assert normalize_price("$1,149.99", "ebay") == 1149.99


class TestNormalizePriceRange:

    def test_range_takes_first(self):
        """Price range '$199.00 to $249.00' should return first price."""
        result = normalize_price("$199.00 to $249.00", "ebay")
        assert result == 199.00

    def test_range_with_dash(self):
        result = normalize_price("$50.00 - $75.00", "ebay")
        assert result == 50.00


# ═══════════════════════════════════════════════════════════════
# 2. validate_price — bounds checking
# ═══════════════════════════════════════════════════════════════


class TestValidatePrice:

    def test_usd_valid(self):
        assert validate_price(29.99, "USD") is True

    def test_usd_too_low(self):
        assert validate_price(0.10, "USD") is False

    def test_usd_too_high(self):
        assert validate_price(99999.0, "USD") is False

    def test_ars_valid_large(self):
        assert validate_price(405188.0, "ARS") is True

    def test_ars_too_low(self):
        assert validate_price(100.0, "ARS") is False

    def test_mxn_valid(self):
        assert validate_price(5299.0, "MXN") is True

    def test_mxn_boundary_low(self):
        assert validate_price(5.0, "MXN") is False  # below min

    def test_eur_valid(self):
        assert validate_price(89.99, "EUR") is True

    def test_zero_price_invalid(self):
        assert validate_price(0.0, "USD") is False

    def test_negative_price_invalid(self):
        assert validate_price(-10.0, "USD") is False


# ═══════════════════════════════════════════════════════════════
# 3. HTML element extractors
# ═══════════════════════════════════════════════════════════════


class TestExtractAmazonPrice:

    def test_offscreen_price(self):
        html = '<div class="a-price"><span class="a-offscreen">$4,299.00</span></div>'
        item = BeautifulSoup(html, "html.parser")
        assert extract_amazon_price(item) == 4299.00

    def test_split_price(self):
        html = """
        <div class="a-price">
            <span class="a-price-whole">18,999</span>
            <span class="a-price-fraction">00</span>
        </div>
        """
        item = BeautifulSoup(html, "html.parser")
        result = extract_amazon_price(item)
        assert result == 18999.00

    def test_no_price_element(self):
        html = '<div class="no-price">Nothing here</div>'
        item = BeautifulSoup(html, "html.parser")
        assert extract_amazon_price(item) is None


class TestExtractEbayPrice:

    def test_standard_usd(self):
        html = '<span class="s-item__price">$229.99</span>'
        item = BeautifulSoup(html, "html.parser")
        assert extract_ebay_price(item) == 229.99

    def test_range_price(self):
        html = '<span class="s-item__price">$199.00 to $249.00</span>'
        item = BeautifulSoup(html, "html.parser")
        assert extract_ebay_price(item) == 199.00

    def test_no_price(self):
        html = '<span class="other">text</span>'
        item = BeautifulSoup(html, "html.parser")
        assert extract_ebay_price(item) is None


class TestExtractMLPrice:

    def test_fraction_only(self):
        html = """
        <div>
            <span class="andes-money-amount__fraction">15999</span>
        </div>
        """
        item = BeautifulSoup(html, "html.parser")
        assert extract_ml_price(item) == 15999.0

    def test_fraction_and_cents(self):
        html = """
        <div>
            <span class="andes-money-amount__fraction">3499</span>
            <span class="andes-money-amount__cents">99</span>
        </div>
        """
        item = BeautifulSoup(html, "html.parser")
        assert extract_ml_price(item) == 3499.99

    def test_no_elements(self):
        html = '<div>No price</div>'
        item = BeautifulSoup(html, "html.parser")
        assert extract_ml_price(item) is None
