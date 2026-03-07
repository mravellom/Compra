"""
E2E Scraper Tests — HTML Mock con Playwright

Carga los 3 fixtures HTML de conftest.py en páginas locales (no ataca webs reales).
Valida:
  - Extracción de título, precio, URL, imagen, snippet
  - Filtrado de "Shop on eBay"
  - DOM variante con <span> anidados
  - Precios complejos: $1,149.99, rangos "$199 to $249", EUR, GBP
  - Unit tests de _parse_price y _parse_currency
"""
import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from scraper.ebay_scraper import EbayScraper


# ─── Playwright fixtures ────────────────────────────────────────────

@pytest_asyncio.fixture
async def browser():
    """Levanta un Chromium headless compartido entre tests."""
    pw = await async_playwright().start()
    br = await pw.chromium.launch(headless=True)
    yield br
    await br.close()
    await pw.stop()


async def page_from_html(browser, html: str):
    """Inyecta HTML en una página local y la retorna."""
    context = await browser.new_context()
    page = await context.new_page()
    await page.set_content(html, wait_until="domcontentloaded")
    return page


def make_scraper() -> EbayScraper:
    """Crea una instancia sin inicializar el browser (solo para _parse_item)."""
    return EbayScraper.__new__(EbayScraper)


# ═══════════════════════════════════════════════════════════════════
# 1. HTML Estándar: extracción básica
# ═══════════════════════════════════════════════════════════════════

class TestStandardHTML:

    @pytest.mark.asyncio
    async def test_extracts_two_valid_items(self, browser, ebay_html_standard):
        """Parsea 2 items válidos y filtra 'Shop on eBay'."""
        page = await page_from_html(browser, ebay_html_standard)
        items = await page.query_selector_all(".srp-results .s-item")
        scraper = make_scraper()

        results = []
        for item in items:
            listing = await scraper._parse_item(item, page)
            if listing:
                results.append(listing)

        assert len(results) == 2

        # Sony
        assert results[0].title == "Sony WH-1000XM4 Wireless Headphones"
        assert results[0].price == 229.99
        assert results[0].currency == "USD"
        assert "123456" in str(results[0].url)

        # AirPods
        assert results[1].title == "Apple AirPods Pro 2nd Generation"
        assert results[1].price == 189.50

        await page.context.close()

    @pytest.mark.asyncio
    async def test_image_url_extracted(self, browser, ebay_html_standard):
        page = await page_from_html(browser, ebay_html_standard)
        items = await page.query_selector_all(".srp-results .s-item")
        listing = await make_scraper()._parse_item(items[0], page)

        assert listing is not None
        assert "fake1.jpg" in str(listing.image_url)
        await page.context.close()

    @pytest.mark.asyncio
    async def test_raw_html_snippet_captured(self, browser, ebay_html_standard):
        page = await page_from_html(browser, ebay_html_standard)
        items = await page.query_selector_all(".srp-results .s-item")
        listing = await make_scraper()._parse_item(items[0], page)

        assert listing is not None
        assert listing.raw_html_snippet is not None
        assert len(listing.raw_html_snippet) > 0
        assert len(listing.raw_html_snippet) <= 500
        await page.context.close()

    @pytest.mark.asyncio
    async def test_shop_on_ebay_filtered(self, browser, ebay_html_standard):
        """El item 'Shop on eBay' debe retornar None."""
        page = await page_from_html(browser, ebay_html_standard)
        items = await page.query_selector_all(".srp-results .s-item")
        # El tercer item es "Shop on eBay"
        listing = await make_scraper()._parse_item(items[2], page)
        assert listing is None
        await page.context.close()


# ═══════════════════════════════════════════════════════════════════
# 2. DOM Variante: títulos en <span> anidados
# ═══════════════════════════════════════════════════════════════════

class TestVariantDOM:

    @pytest.mark.asyncio
    async def test_nested_span_title(self, browser, ebay_html_variant):
        """Título envuelto en <span class='BOLD'><span> se extrae correctamente."""
        page = await page_from_html(browser, ebay_html_variant)
        items = await page.query_selector_all(".srp-results .s-item")
        listing = await make_scraper()._parse_item(items[0], page)

        assert listing is not None
        assert "Nintendo Switch OLED Model" in listing.title
        assert listing.price == 299.0
        assert "555555" in str(listing.url)
        await page.context.close()

    @pytest.mark.asyncio
    async def test_price_with_thousands_comma(self, browser, ebay_html_variant):
        """$1,149.99 se parsea correctamente como 1149.99."""
        page = await page_from_html(browser, ebay_html_variant)
        items = await page.query_selector_all(".srp-results .s-item")
        listing = await make_scraper()._parse_item(items[1], page)

        assert listing is not None
        assert listing.price == 1149.99
        assert listing.title == "Samsung Galaxy S24 Ultra 256GB"
        await page.context.close()


# ═══════════════════════════════════════════════════════════════════
# 3. Precios internacionales: rangos, EUR, GBP
# ═══════════════════════════════════════════════════════════════════

class TestInternationalPrices:

    @pytest.mark.asyncio
    async def test_price_range_takes_first_value(self, browser, ebay_html_price_range):
        """'$199.00 to $249.00' → toma $199.00."""
        page = await page_from_html(browser, ebay_html_price_range)
        items = await page.query_selector_all(".srp-results .s-item")
        listing = await make_scraper()._parse_item(items[0], page)

        assert listing is not None
        assert listing.price == 199.0
        assert listing.currency == "USD"
        await page.context.close()

    @pytest.mark.asyncio
    async def test_eur_currency(self, browser, ebay_html_price_range):
        """'EUR 89.99' → precio=89.99, currency=EUR."""
        page = await page_from_html(browser, ebay_html_price_range)
        items = await page.query_selector_all(".srp-results .s-item")
        listing = await make_scraper()._parse_item(items[1], page)

        assert listing is not None
        assert listing.price == 89.99
        assert listing.currency == "EUR"
        await page.context.close()

    @pytest.mark.asyncio
    async def test_gbp_currency(self, browser, ebay_html_price_range):
        """'£59.99' → precio=59.99, currency=GBP."""
        page = await page_from_html(browser, ebay_html_price_range)
        items = await page.query_selector_all(".srp-results .s-item")
        listing = await make_scraper()._parse_item(items[2], page)

        assert listing is not None
        assert listing.price == 59.99
        assert listing.currency == "GBP"
        await page.context.close()


# ═══════════════════════════════════════════════════════════════════
# 4. Unit tests: _parse_price (formatos complejos)
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
    def test_parse_price_formats(self, text, expected):
        assert EbayScraper._parse_price(text) == pytest.approx(expected, abs=0.01)

    def test_parse_price_no_number(self):
        assert EbayScraper._parse_price("Free") is None

    def test_parse_price_empty(self):
        assert EbayScraper._parse_price("") is None


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
    def test_parse_currency_formats(self, text, expected):
        assert EbayScraper._parse_currency(text) == expected
