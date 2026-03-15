"""
E2E Scraper Tests — HTML Parsing con BeautifulSoup

Carga los 3 fixtures HTML de conftest.py y parsea con BeautifulSoup.
Valida:
  - Extracción de título, precio, URL, imagen
  - Filtrado de "Shop on eBay"
  - DOM variante con <span> anidados
  - Precios complejos: $1,149.99, rangos "$199 to $249", EUR, GBP
  - Unit tests de extract_ebay_price y _parse_currency
"""
import pytest
from bs4 import BeautifulSoup

from scraper.ebay_scraper import EbayScraper
from scraper.price_parser import extract_ebay_price


# ─── Helpers ────────────────────────────────────────────────────────

def parse_items(html: str):
    """Parse HTML fixture and return list of .s-item Tags."""
    soup = BeautifulSoup(html, "html.parser")
    return soup.select(".srp-results .s-item")


def make_scraper() -> EbayScraper:
    """Crea una instancia sin inicializar red (solo para _parse_item)."""
    return EbayScraper.__new__(EbayScraper)


@pytest.fixture
def make_item():
    """Factory fixture: creates a .s-item Tag with a given price string."""
    def _make(price_html: str) -> "Tag":
        html = f'''
        <div class="s-item">
            <a class="s-item__link" href="https://www.ebay.com/itm/999999">
                <div class="s-item__title">Test Product</div>
            </a>
            <span class="s-item__price">{price_html}</span>
        </div>
        '''
        soup = BeautifulSoup(html, "html.parser")
        return soup.select_one(".s-item")
    return _make


# ═══════════════════════════════════════════════════════════════════
# 1. HTML Estándar: extracción básica
# ═══════════════════════════════════════════════════════════════════

class TestStandardHTML:

    def test_extracts_two_valid_items(self, ebay_html_standard):
        """Parsea 2 items válidos y filtra 'Shop on eBay'."""
        items = parse_items(ebay_html_standard)
        scraper = make_scraper()

        results = [scraper._parse_item(item) for item in items]
        results = [r for r in results if r is not None]

        assert len(results) == 2

        # Sony
        assert results[0].title == "Sony WH-1000XM4 Wireless Headphones"
        assert results[0].price == 229.99
        assert results[0].currency == "USD"
        assert "123456" in str(results[0].url)

        # AirPods
        assert results[1].title == "Apple AirPods Pro 2nd Generation"
        assert results[1].price == 189.50

    def test_image_url_extracted(self, ebay_html_standard):
        items = parse_items(ebay_html_standard)
        listing = make_scraper()._parse_item(items[0])

        assert listing is not None
        assert "fake1.jpg" in str(listing.image_url)

    def test_raw_html_snippet_captured(self, ebay_html_standard):
        """After refactor, raw_html_snippet is no longer populated by _parse_item."""
        items = parse_items(ebay_html_standard)
        listing = make_scraper()._parse_item(items[0])

        assert listing is not None
        # raw_html_snippet is no longer set in the BeautifulSoup-based parser
        assert listing.raw_html_snippet is None

    def test_shop_on_ebay_filtered(self, ebay_html_standard):
        """El item 'Shop on eBay' debe retornar None."""
        items = parse_items(ebay_html_standard)
        # El tercer item es "Shop on eBay"
        listing = make_scraper()._parse_item(items[2])
        assert listing is None


# ═══════════════════════════════════════════════════════════════════
# 2. DOM Variante: títulos en <span> anidados
# ═══════════════════════════════════════════════════════════════════

class TestVariantDOM:

    def test_nested_span_title(self, ebay_html_variant):
        """Título envuelto en <span class='BOLD'><span> se extrae correctamente."""
        items = parse_items(ebay_html_variant)
        listing = make_scraper()._parse_item(items[0])

        assert listing is not None
        assert "Nintendo Switch OLED Model" in listing.title
        assert listing.price == 299.0
        assert "555555" in str(listing.url)

    def test_price_with_thousands_comma(self, ebay_html_variant):
        """$1,149.99 se parsea correctamente como 1149.99."""
        items = parse_items(ebay_html_variant)
        listing = make_scraper()._parse_item(items[1])

        assert listing is not None
        assert listing.price == 1149.99
        assert listing.title == "Samsung Galaxy S24 Ultra 256GB"


# ═══════════════════════════════════════════════════════════════════
# 3. Precios internacionales: rangos, EUR, GBP
# ═══════════════════════════════════════════════════════════════════

class TestInternationalPrices:

    def test_price_range_takes_first_value(self, ebay_html_price_range):
        """'$199.00 to $249.00' → toma $199.00."""
        items = parse_items(ebay_html_price_range)
        listing = make_scraper()._parse_item(items[0])

        assert listing is not None
        assert listing.price == 199.0
        assert listing.currency == "USD"

    def test_eur_currency(self, ebay_html_price_range):
        """'EUR 89.99' → precio=89.99, currency=EUR."""
        items = parse_items(ebay_html_price_range)
        listing = make_scraper()._parse_item(items[1])

        assert listing is not None
        assert listing.price == 89.99
        assert listing.currency == "EUR"

    def test_gbp_currency(self, ebay_html_price_range):
        """'£59.99' → precio=59.99, currency=GBP."""
        items = parse_items(ebay_html_price_range)
        listing = make_scraper()._parse_item(items[2])

        assert listing is not None
        assert listing.price == 59.99
        assert listing.currency == "GBP"


# ═══════════════════════════════════════════════════════════════════
# 4. Unit tests: extract_ebay_price (formatos complejos)
# ═══════════════════════════════════════════════════════════════════

class TestParsePriceUnit:

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("$29.99", 29.99),
            ("$1,149.99", 1149.99),
            ("$1,299.00", 1299.0),
            ("$10.00 to $20.00", 10.0),         # rango → primer precio
            ("$100", 100.0),                      # entero
            ("EUR 45.99", 45.99),
            ("\u00a359.99", 59.99),               # £59.99
            ("$0.99", 0.99),                      # centavos
            ("$12,345,678.99", 12345678.99),      # millones
        ],
        ids=[
            "simple-usd",
            "thousands-comma",
            "thousands-comma-2",
            "price-range",
            "integer-price",
            "eur-text",
            "gbp-symbol",
            "under-dollar",
            "millions",
        ],
    )
    def test_parse_price_formats(self, make_item, text, expected):
        item = make_item(text)
        assert extract_ebay_price(item) == pytest.approx(expected, abs=0.01)

    def test_parse_price_no_number(self, make_item):
        item = make_item("Free")
        assert extract_ebay_price(item) is None

    def test_parse_price_empty(self, make_item):
        item = make_item("")
        assert extract_ebay_price(item) is None


# ═══════════════════════════════════════════════════════════════════
# 5. Unit tests: _parse_currency
# ═══════════════════════════════════════════════════════════════════

class TestParseCurrencyUnit:

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("$29.99", "USD"),
            ("EUR 45.99", "EUR"),
            ("\u20ac45.99", "EUR"),    # €
            ("\u00a359.99", "GBP"),    # £
            ("GBP 100.00", "GBP"),
            ("29.99", "USD"),          # sin símbolo → default USD
        ],
        ids=["usd-dollar", "eur-text", "eur-symbol", "gbp-symbol", "gbp-text", "default-usd"],
    )
    def test_parse_currency_formats(self, make_item, text, expected):
        item = make_item(text)
        assert EbayScraper._parse_currency(item) == expected
