"""
Integration Tests — eBay HTML Parsing.

Uses HTML fixtures from conftest.py to test the eBay scraper's
_parse_item method against various DOM structures.
"""
import pytest
from bs4 import BeautifulSoup

from scraper.ebay_scraper import EbayScraper


def _parse_items(html: str) -> list:
    """Parse HTML and return list of RawListings using EbayScraper."""
    scraper = EbayScraper()
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select(".s-item")
    results = []
    for item in items:
        parsed = scraper._parse_item(item)
        if parsed:
            results.append(parsed)
    return results


class TestEbayStandardParsing:
    """Tests using ebay_html_standard fixture from conftest."""

    def test_extracts_valid_items(self, ebay_html_standard):
        results = _parse_items(ebay_html_standard)
        assert len(results) == 2  # "Shop on eBay" filtered out

    def test_titles_extracted(self, ebay_html_standard):
        results = _parse_items(ebay_html_standard)
        assert results[0].title == "Sony WH-1000XM4 Wireless Headphones"
        assert results[1].title == "Apple AirPods Pro 2nd Generation"

    def test_prices_extracted(self, ebay_html_standard):
        results = _parse_items(ebay_html_standard)
        assert results[0].price == 229.99
        assert results[1].price == 189.50

    def test_urls_extracted(self, ebay_html_standard):
        results = _parse_items(ebay_html_standard)
        assert "123456" in str(results[0].url)
        assert "789012" in str(results[1].url)

    def test_marketplace_id(self, ebay_html_standard):
        results = _parse_items(ebay_html_standard)
        for r in results:
            assert r.marketplace_id == "ebay"

    def test_shop_on_ebay_filtered(self, ebay_html_standard):
        results = _parse_items(ebay_html_standard)
        titles = [r.title for r in results]
        assert "Shop on eBay" not in titles


class TestEbayVariantParsing:
    """Tests using ebay_html_variant fixture — nested title spans."""

    def test_nested_title_extracted(self, ebay_html_variant):
        results = _parse_items(ebay_html_variant)
        assert len(results) == 2
        assert "Nintendo Switch" in results[0].title
        assert "Samsung Galaxy" in results[1].title

    def test_thousands_separator_price(self, ebay_html_variant):
        results = _parse_items(ebay_html_variant)
        assert results[1].price == 1149.99


class TestEbayPriceRange:
    """Tests using ebay_html_price_range fixture — EUR, GBP, ranges."""

    def test_range_takes_first_price(self, ebay_html_price_range):
        results = _parse_items(ebay_html_price_range)
        bose = next(r for r in results if "Bose" in r.title)
        assert bose.price == 199.00

    def test_eur_price(self, ebay_html_price_range):
        results = _parse_items(ebay_html_price_range)
        jbl = next(r for r in results if "JBL" in r.title)
        assert jbl.price == 89.99

    def test_gbp_price(self, ebay_html_price_range):
        results = _parse_items(ebay_html_price_range)
        razer = next(r for r in results if "Razer" in r.title)
        assert razer.price == 59.99


class TestEbayConditionDetection:
    """Test condition detection from SECONDARY_INFO."""

    def test_refurbished_detected(self):
        from tests.fixtures.html_samples import EBAY_SEARCH_WITH_SHIPPING
        results = _parse_items(EBAY_SEARCH_WITH_SHIPPING)
        refurb = next(r for r in results if "Refurbished" in r.title)
        assert refurb.condition == "refurbished"

    def test_new_detected(self):
        from tests.fixtures.html_samples import EBAY_SEARCH_WITH_SHIPPING
        results = _parse_items(EBAY_SEARCH_WITH_SHIPPING)
        new_item = next(r for r in results if "Refurbished" not in r.title)
        assert new_item.condition == "new"


class TestEbayMalformedHTML:
    """Test graceful handling of broken/empty HTML."""

    def test_empty_items_skipped(self):
        from tests.fixtures.html_samples import MALFORMED_HTML
        results = _parse_items(MALFORMED_HTML)
        assert len(results) == 0  # All malformed items should be skipped

    def test_empty_results_page(self):
        from tests.fixtures.html_samples import EBAY_EMPTY_RESULTS
        results = _parse_items(EBAY_EMPTY_RESULTS)
        assert len(results) == 0  # "Shop on eBay" filtered
