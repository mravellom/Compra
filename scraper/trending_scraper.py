"""
Trending / best-seller product discovery.

Scrapes high-visibility sections of each marketplace:
- Best Sellers
- Most Wished For
- Deals of the Day
- Trending / Featured

These sections contain high-liquidity products with the best
arbitrage potential due to active demand.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from .category_config import get_category_url
from .price_parser import extract_amazon_price, extract_ml_price, validate_price
from .rate_limiter import RateLimiter
from .schemas import RawListing
from .stealth import StealthSession, ProxyPool

logger = logging.getLogger(__name__)


# ── Trending page URLs per marketplace ─────────────────────────
TRENDING_PAGES: dict[str, list[dict[str, str]]] = {
    "amazon": [
        {
            "name": "best_sellers_electronics",
            "url": "https://www.amazon.com.mx/gp/bestsellers/electronics/",
        },
        {
            "name": "best_sellers_computers",
            "url": "https://www.amazon.com.mx/gp/bestsellers/computers/",
        },
        {
            "name": "best_sellers_videogames",
            "url": "https://www.amazon.com.mx/gp/bestsellers/videogames/",
        },
        {
            "name": "most_wished_electronics",
            "url": "https://www.amazon.com.mx/gp/most-wished-for/electronics/",
        },
        {
            "name": "movers_shakers_electronics",
            "url": "https://www.amazon.com.mx/gp/movers-and-shakers/electronics/",
        },
    ],
    "amazon_us": [
        {
            "name": "best_sellers_electronics",
            "url": "https://www.amazon.com/gp/bestsellers/electronics/",
        },
        {
            "name": "best_sellers_computers",
            "url": "https://www.amazon.com/gp/bestsellers/computers/",
        },
        {
            "name": "best_sellers_videogames",
            "url": "https://www.amazon.com/gp/bestsellers/videogames/",
        },
        {
            "name": "most_wished_electronics",
            "url": "https://www.amazon.com/gp/most-wished-for/electronics/",
        },
        {
            "name": "new_releases_electronics",
            "url": "https://www.amazon.com/gp/new-releases/electronics/",
        },
        {
            "name": "movers_shakers_electronics",
            "url": "https://www.amazon.com/gp/movers-and-shakers/electronics/",
        },
    ],
    "mercadolibre_mx": [
        {
            "name": "ofertas_tecnologia",
            "url": "https://www.mercadolibre.com.mx/ofertas/tecnologia",
        },
        {
            "name": "mas_vendidos_electronicos",
            "url": "https://www.mercadolibre.com.mx/mas-vendidos/MLM1051",
        },
        {
            "name": "mas_vendidos_celulares",
            "url": "https://www.mercadolibre.com.mx/mas-vendidos/MLM1055",
        },
        {
            "name": "mas_vendidos_computacion",
            "url": "https://www.mercadolibre.com.mx/mas-vendidos/MLM1648",
        },
    ],
    "mercadolibre_ar": [
        {
            "name": "ofertas_tecnologia",
            "url": "https://www.mercadolibre.com.ar/ofertas/tecnologia",
        },
        {
            "name": "mas_vendidos_electronicos",
            "url": "https://www.mercadolibre.com.ar/mas-vendidos/MLA1051",
        },
        {
            "name": "mas_vendidos_celulares",
            "url": "https://www.mercadolibre.com.ar/mas-vendidos/MLA1055",
        },
        {
            "name": "mas_vendidos_computacion",
            "url": "https://www.mercadolibre.com.ar/mas-vendidos/MLA1648",
        },
    ],
    "mercadolibre_cl": [
        {
            "name": "ofertas_tecnologia",
            "url": "https://www.mercadolibre.cl/ofertas/tecnologia",
        },
        {
            "name": "mas_vendidos_electronicos",
            "url": "https://www.mercadolibre.cl/mas-vendidos/MLC1051",
        },
    ],
    "mercadolibre_co": [
        {
            "name": "ofertas_tecnologia",
            "url": "https://www.mercadolibre.com.co/ofertas/tecnologia",
        },
        {
            "name": "mas_vendidos_electronicos",
            "url": "https://www.mercadolibre.com.co/mas-vendidos/MCO1051",
        },
    ],
}

# Currency per marketplace
_CURRENCIES: dict[str, str] = {
    "amazon": "MXN",
    "amazon_us": "USD",
    "mercadolibre_mx": "MXN",
    "mercadolibre_ar": "ARS",
    "mercadolibre_cl": "CLP",
    "mercadolibre_co": "COP",
}


class TrendingScraper:
    """Scrapes trending/bestseller pages across marketplaces."""

    def __init__(
        self,
        rate_limiter: RateLimiter | None = None,
        proxy_pool: ProxyPool | None = None,
    ):
        self._rate_limiter = rate_limiter or RateLimiter(requests_per_second=0.4, burst=2)
        self._proxy_pool = proxy_pool

    async def scrape_all_trending(
        self,
        marketplaces: list[str] | None = None,
        max_pages_per_section: int = 2,
    ) -> list[RawListing]:
        """
        Scrape trending pages for specified marketplaces.

        Args:
            marketplaces: List of marketplace IDs. None = all available.
            max_pages_per_section: Pages to crawl per trending section.

        Returns:
            Combined list of all trending listings.
        """
        if marketplaces is None:
            marketplaces = list(TRENDING_PAGES.keys())

        all_listings: list[RawListing] = []

        for mkt_id in marketplaces:
            pages = TRENDING_PAGES.get(mkt_id, [])
            if not pages:
                continue

            for page_info in pages:
                try:
                    listings = await self._scrape_trending_page(
                        mkt_id, page_info, max_pages_per_section,
                    )
                    all_listings.extend(listings)
                    logger.info(
                        "[trending] %s/%s: %d listings",
                        mkt_id, page_info["name"], len(listings),
                    )
                except Exception:
                    logger.error(
                        "Error scraping trending %s/%s",
                        mkt_id, page_info["name"], exc_info=True,
                    )

        logger.info("Trending scrape total: %d listings", len(all_listings))
        return all_listings

    async def _scrape_trending_page(
        self,
        marketplace_id: str,
        page_info: dict[str, str],
        max_pages: int,
    ) -> list[RawListing]:
        """Scrape a single trending page with pagination."""
        if marketplace_id.startswith("amazon"):
            return await self._scrape_amazon_trending(
                marketplace_id, page_info, max_pages,
            )
        elif marketplace_id.startswith("mercadolibre"):
            return await self._scrape_ml_trending(
                marketplace_id, page_info, max_pages,
            )
        return []

    async def _scrape_amazon_trending(
        self,
        marketplace_id: str,
        page_info: dict[str, str],
        max_pages: int,
    ) -> list[RawListing]:
        """Parse Amazon bestseller/trending pages."""
        listings: list[RawListing] = []
        currency = _CURRENCIES.get(marketplace_id, "USD")
        domain = "amazon_us" if marketplace_id == "amazon_us" else "amazon"

        extra_headers = {}
        extra_cookies = {}
        accept_lang = "es-MX,es;q=0.9,en;q=0.8"
        if marketplace_id == "amazon_us":
            extra_headers = {"X-Forwarded-For": "72.21.215.1"}
            extra_cookies = {"lc-main": "en_US", "i18n-prefs": "USD"}
            accept_lang = "en-US,en;q=0.9"

        async with StealthSession(
            proxy_pool=self._proxy_pool,
            rate_limiter=self._rate_limiter,
            accept_language=accept_lang,
            base_delay=2.5,
            extra_headers=extra_headers,
            extra_cookies=extra_cookies,
        ) as session:
            current_url = page_info["url"]

            for page in range(1, max_pages + 1):
                if not current_url:
                    break

                try:
                    resp = await session.fetch(current_url, domain=domain)
                except Exception:
                    logger.error("Failed trending fetch: %s", current_url[:80], exc_info=True)
                    break

                soup = BeautifulSoup(resp.text, "html.parser")

                # Amazon bestseller items use different selectors
                items = soup.select(
                    '[data-component-type="s-search-result"], '
                    '.zg-item-immersion, '
                    '#gridItemRoot, '
                    '.p13n-sc-uncoverable-faceout'
                )

                if not items:
                    break

                for item in items:
                    try:
                        listing = self._parse_amazon_trending_item(
                            item, marketplace_id, currency,
                        )
                        if listing:
                            listings.append(listing)
                    except Exception:
                        continue

                # Pagination — Amazon bestsellers use "Next" link
                next_link = soup.select_one('li.a-last a, a[aria-label="Next"]')
                if next_link and next_link.get("href"):
                    href = next_link["href"]
                    if href.startswith("/"):
                        base = "https://www.amazon.com" if marketplace_id == "amazon_us" else "https://www.amazon.com.mx"
                        current_url = base + href
                    else:
                        current_url = href
                else:
                    break

        return listings

    def _parse_amazon_trending_item(
        self,
        item,
        marketplace_id: str,
        currency: str,
    ) -> RawListing | None:
        """Parse a single Amazon trending/bestseller item."""
        # Title
        title_el = item.select_one(
            '.p13n-sc-truncate, ._cDEzb_p13n-sc-css-line-clamp-3_g3dy1, '
            'a.a-link-normal span, .a-size-base-plus, '
            '[data-component-type="s-search-result"] h2 span'
        )
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if len(title) < 5:
            return None

        # Price
        price_el = item.select_one(
            '.a-price .a-offscreen, .p13n-sc-price, '
            '._cDEzb_p13n-sc-price_3mJ9Z, .a-price-whole'
        )
        if not price_el:
            return None

        price_text = price_el.get_text(strip=True)
        price = extract_amazon_price(price_text)
        if not price or not validate_price(price, currency):
            return None

        # URL
        link_el = item.select_one('a.a-link-normal[href*="/dp/"], a[href*="/dp/"]')
        if not link_el:
            return None
        href = link_el.get("href", "")
        asin_match = re.search(r"/dp/([A-Z0-9]{10})", href)
        if not asin_match:
            return None

        base = "https://www.amazon.com" if marketplace_id == "amazon_us" else "https://www.amazon.com.mx"
        url = f"{base}/dp/{asin_match.group(1)}"

        # Image
        img_el = item.select_one("img")
        image_url = img_el.get("src") if img_el else None

        # Rating
        rating_el = item.select_one('.a-icon-alt, [aria-label*="stars"]')
        rating = None
        if rating_el:
            rating_text = rating_el.get_text(strip=True) or rating_el.get("aria-label", "")
            rating_match = re.search(r"([\d.]+)", rating_text)
            if rating_match:
                rating = float(rating_match.group(1))

        return RawListing(
            title=title,
            price=price,
            currency=currency,
            url=url,
            marketplace_id=marketplace_id,
            image_url=image_url,
            seller_rating=rating,
            condition="new",
        )

    async def _scrape_ml_trending(
        self,
        marketplace_id: str,
        page_info: dict[str, str],
        max_pages: int,
    ) -> list[RawListing]:
        """Parse MercadoLibre trending/offers/bestseller pages."""
        listings: list[RawListing] = []
        currency = _CURRENCIES.get(marketplace_id, "MXN")
        needs_js = marketplace_id in ("mercadolibre_cl", "mercadolibre_co")

        if needs_js:
            return await self._scrape_ml_trending_playwright(
                marketplace_id, page_info, currency,
            )

        async with StealthSession(
            proxy_pool=self._proxy_pool,
            rate_limiter=self._rate_limiter,
            accept_language="es-MX,es;q=0.9,en;q=0.8",
            base_delay=2.0,
        ) as session:
            current_url = page_info["url"]

            for page in range(1, max_pages + 1):
                if not current_url:
                    break

                try:
                    resp = await session.fetch(current_url, domain="mercadolibre")
                except Exception:
                    logger.error("ML trending fetch failed: %s", current_url[:80], exc_info=True)
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                items = soup.select(
                    '.ui-search-layout__item, '
                    '.promotion-item, '
                    '.andes-card, '
                    '[class*="item__info"]'
                )

                if not items:
                    break

                for item in items:
                    try:
                        listing = self._parse_ml_trending_item(
                            item, marketplace_id, currency,
                        )
                        if listing:
                            listings.append(listing)
                    except Exception:
                        continue

                # Pagination
                next_link = soup.select_one('li.andes-pagination__button--next a')
                if next_link and next_link.get("href"):
                    current_url = next_link["href"]
                else:
                    break

        return listings

    async def _scrape_ml_trending_playwright(
        self,
        marketplace_id: str,
        page_info: dict[str, str],
        currency: str,
    ) -> list[RawListing]:
        """Playwright fallback for ML sites with bot challenge (CL/CO)."""
        try:
            from .browser import get_shared_browser
        except ImportError:
            logger.warning("Playwright not available for ML trending on %s", marketplace_id)
            return []

        listings: list[RawListing] = []
        browser = await get_shared_browser()

        locale = "es-CL" if marketplace_id == "mercadolibre_cl" else "es-CO"
        tz = "America/Santiago" if marketplace_id == "mercadolibre_cl" else "America/Bogota"
        page = await browser.new_page(locale=locale, timezone_id=tz)

        try:
            await page.goto(page_info["url"], wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
            content = await page.content()
            soup = BeautifulSoup(content, "html.parser")

            items = soup.select(
                '.ui-search-layout__item, .promotion-item, .andes-card'
            )
            for item in items:
                try:
                    listing = self._parse_ml_trending_item(item, marketplace_id, currency)
                    if listing:
                        listings.append(listing)
                except Exception:
                    continue
        finally:
            await page.close()

        return listings

    def _parse_ml_trending_item(
        self,
        item,
        marketplace_id: str,
        currency: str,
    ) -> RawListing | None:
        """Parse a single ML trending/offer item."""
        # Title
        title_el = item.select_one(
            '.ui-search-item__title, .promotion-item__title, '
            'a.ui-search-link__title-card, h2'
        )
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if len(title) < 5:
            return None

        # Price — ML format
        price = extract_ml_price(item, marketplace_id)
        if not price or not validate_price(price, currency):
            return None

        # URL
        link_el = item.select_one('a[href*="mercadolibre"], a.ui-search-link')
        if not link_el:
            return None
        url = link_el.get("href", "")
        if not url or "mercadolibre" not in url:
            return None
        # Clean tracking params
        url = url.split("#")[0].split("?")[0] if "?" in url else url.split("#")[0]

        # Image
        img_el = item.select_one("img")
        image_url = None
        if img_el:
            image_url = img_el.get("data-src") or img_el.get("src")
            if image_url and "http" not in image_url:
                image_url = None

        # Free shipping
        shipping_el = item.select_one(
            '.ui-search-item__shipping, [class*="free-shipping"]'
        )
        is_free_shipping = bool(shipping_el)

        return RawListing(
            title=title,
            price=price,
            currency=currency,
            url=url,
            marketplace_id=marketplace_id,
            image_url=image_url,
            condition="new",
            is_free_shipping=is_free_shipping,
        )
