"""
MercadoLibre category crawler with pagination.
Reuses the same parsing logic from mercadolibre_scraper.
Uses StealthSession for AR/MX (httpx) and Playwright for CL/CO (bot challenge).
"""
import asyncio
import logging
import re

from bs4 import BeautifulSoup

from .category_config import get_category_url
from .price_parser import extract_ml_price, validate_price
from .rate_limiter import RateLimiter
from .schemas import RawListing
from .stealth import StealthSession, ProxyPool

logger = logging.getLogger(__name__)

ML_SITE_CONFIG = {
    "mercadolibre_ar": {
        "currency": "ARS",
        "lang": "es-AR,es;q=0.9,en;q=0.8",
        "needs_js": False,
    },
    "mercadolibre_mx": {
        "currency": "MXN",
        "lang": "es-MX,es;q=0.9,en;q=0.8",
        "needs_js": False,
    },
    "mercadolibre_cl": {
        "currency": "CLP",
        "lang": "es-CL,es;q=0.9,en;q=0.8",
        "needs_js": True,
        "locale": "es-CL",
        "timezone": "America/Santiago",
    },
    "mercadolibre_co": {
        "currency": "COP",
        "lang": "es-CO,es;q=0.9,en;q=0.8",
        "needs_js": True,
        "locale": "es-CO",
        "timezone": "America/Bogota",
    },
}


class MLCategoryCrawler:
    """Crawls MercadoLibre category pages with pagination and anti-blocking."""

    def __init__(
        self,
        site_id: str = "mercadolibre_ar",
        rate_limiter: RateLimiter | None = None,
        proxy_pool: ProxyPool | None = None,
    ):
        if site_id not in ML_SITE_CONFIG:
            raise ValueError(f"Unknown ML site: {site_id}")
        self.marketplace_id = site_id
        cfg = ML_SITE_CONFIG[site_id]
        self._currency = cfg["currency"]
        self._lang = cfg["lang"]
        self._needs_js = cfg.get("needs_js", False)
        self._locale = cfg.get("locale", "es-MX")
        self._timezone = cfg.get("timezone", "America/Mexico_City")
        self._rate_limiter = rate_limiter or RateLimiter(requests_per_second=0.5, burst=2)
        self._proxy_pool = proxy_pool

    async def crawl_category(
        self, category_slug: str, max_pages: int = 3
    ) -> list[RawListing]:
        """Crawl a category, paginating up to max_pages."""
        if self._needs_js:
            return await self._crawl_with_playwright(category_slug, max_pages)
        return await self._crawl_with_httpx(category_slug, max_pages)

    async def _crawl_with_httpx(
        self, category_slug: str, max_pages: int
    ) -> list[RawListing]:
        """httpx path for AR/MX (no bot challenge)."""
        base_url = get_category_url(self.marketplace_id, category_slug)
        if not base_url:
            logger.warning(
                "No category mapping for '%s' on %s", category_slug, self.marketplace_id
            )
            return []

        all_listings: list[RawListing] = []
        seen_urls: set[str] = set()

        async with StealthSession(
            proxy_pool=self._proxy_pool,
            rate_limiter=self._rate_limiter,
            accept_language=self._lang,
            base_delay=2.0,
            skip_accept_encoding=True,
        ) as session:
            current_url: str | None = base_url

            for page in range(1, max_pages + 1):
                if not current_url:
                    break

                logger.info(
                    "[%s] Category '%s' page %d/%d: %s",
                    self.marketplace_id, category_slug, page, max_pages, current_url[:100],
                )

                try:
                    resp = await session.fetch(
                        current_url, domain=self.marketplace_id,
                    )
                except Exception:
                    logger.error(
                        "Failed to fetch page %d of '%s' on %s",
                        page, category_slug, self.marketplace_id, exc_info=True,
                    )
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                items = soup.select(".ui-search-layout__item")

                if not items:
                    logger.info("No items on page %d, stopping pagination", page)
                    break

                page_count = 0
                for item in items:
                    listing = self._parse_item(item)
                    if listing and str(listing.url) not in seen_urls:
                        seen_urls.add(str(listing.url))
                        all_listings.append(listing)
                        page_count += 1

                logger.info(
                    "[%s] Page %d: %d new listings (total: %d)",
                    self.marketplace_id, page, page_count, len(all_listings),
                )

                current_url = self._find_next_page(soup)

        return all_listings

    async def _crawl_with_playwright(
        self, category_slug: str, max_pages: int
    ) -> list[RawListing]:
        """Playwright path for CL/CO (bot challenge sites)."""
        from .browser import get_shared_browser

        base_url = get_category_url(self.marketplace_id, category_slug)
        if not base_url:
            logger.warning(
                "No category mapping for '%s' on %s", category_slug, self.marketplace_id
            )
            return []

        all_listings: list[RawListing] = []
        seen_urls: set[str] = set()
        current_url: str | None = base_url

        browser_mgr = await get_shared_browser()

        for page_num in range(1, max_pages + 1):
            if not current_url:
                break

            logger.info(
                "[%s][Playwright] Category '%s' page %d/%d: %s",
                self.marketplace_id, category_slug, page_num, max_pages, current_url[:100],
            )

            page = await browser_mgr.new_page(
                locale=self._locale,
                timezone_id=self._timezone,
            )

            try:
                await page.goto(current_url, wait_until="domcontentloaded", timeout=30000)

                # Wait for bot challenge to resolve
                try:
                    await page.wait_for_selector(
                        ".ui-search-layout__item",
                        timeout=20000,
                    )
                except Exception:
                    content = await page.content()
                    if len(content) < 5000 and "_bmstate" in content:
                        logger.warning(
                            "[%s] Bot challenge on page %d, retrying...",
                            self.marketplace_id, page_num,
                        )
                        await asyncio.sleep(5)
                        try:
                            await page.wait_for_selector(
                                ".ui-search-layout__item",
                                timeout=15000,
                            )
                        except Exception:
                            logger.warning(
                                "[%s] Could not bypass bot challenge for '%s' page %d",
                                self.marketplace_id, category_slug, page_num,
                            )
                            break
                    else:
                        logger.info("No items on page %d (Playwright), stopping", page_num)
                        break

                html = await page.content()

            except Exception:
                logger.error(
                    "[%s][Playwright] Error on page %d of '%s'",
                    self.marketplace_id, page_num, category_slug, exc_info=True,
                )
                break
            finally:
                await page.close()

            soup = BeautifulSoup(html, "html.parser")
            items = soup.select(".ui-search-layout__item")

            if not items:
                logger.info("No items on page %d, stopping", page_num)
                break

            page_count = 0
            for item in items:
                listing = self._parse_item(item)
                if listing and str(listing.url) not in seen_urls:
                    seen_urls.add(str(listing.url))
                    all_listings.append(listing)
                    page_count += 1

            logger.info(
                "[%s] Page %d: %d new listings (total: %d)",
                self.marketplace_id, page_num, page_count, len(all_listings),
            )

            current_url = self._find_next_page(soup)

            # Delay between Playwright pages
            await asyncio.sleep(2.0)

        return all_listings

    def _find_next_page(self, soup: BeautifulSoup) -> str | None:
        """Extract the 'next page' URL from ML pagination."""
        next_link = soup.select_one("li.andes-pagination__button--next a")
        if next_link and next_link.get("href"):
            return next_link["href"]
        return None

    def _parse_item(self, item) -> RawListing | None:
        title_el = item.select_one(".poly-component__title")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if not title:
            return None

        # Price — use robust extractor that handles fraction + cents elements
        price = extract_ml_price(item)
        if price is None:
            return None

        if not validate_price(price, self._currency, self.marketplace_id, title):
            return None

        link_el = item.select_one("a[href*='mercadolibre']")
        if not link_el:
            return None
        url = link_el.get("href", "")
        if not url:
            return None

        img_el = item.select_one("img")
        image_url = None
        if img_el:
            image_url = img_el.get("src") or img_el.get("data-src")

        reviews_count = 0
        reviews_el = item.select_one(".poly-reviews__total")
        if reviews_el:
            reviews_text = re.sub(r"[^\d]", "", reviews_el.get_text(strip=True))
            reviews_count = int(reviews_text) if reviews_text else 0

        seller_rating = None
        rating_el = item.select_one(".poly-reviews__rating")
        if rating_el:
            m = re.search(r"([\d.]+)", rating_el.get_text(strip=True))
            if m:
                seller_rating = float(m.group(1))

        is_free_shipping = bool(
            item.select_one(".poly-component__shipping .poly-component__shipped-text")
            or item.select_one("[class*='free-shipping']")
        )

        condition = self._detect_condition(title)

        sales_count = 0
        sold_el = item.select_one(".poly-component__sold")
        if sold_el:
            sold_text = re.sub(r"[^\d]", "", sold_el.get_text(strip=True))
            sales_count = int(sold_text) if sold_text else 0

        seller_name = None
        seller_el = item.select_one(".poly-component__seller")
        if seller_el:
            seller_name = seller_el.get_text(strip=True) or None

        return RawListing(
            title=title,
            price=price,
            currency=self._currency,
            url=url,
            marketplace_id=self.marketplace_id,
            image_url=image_url,
            reviews_count=reviews_count,
            seller_rating=seller_rating,
            is_free_shipping=is_free_shipping,
            condition=condition,
            sales_count=sales_count,
            seller_name=seller_name,
        )

    @staticmethod
    def _detect_condition(title: str) -> str:
        lower = title.lower()
        if any(w in lower for w in ("reacondicionado", "refurbished", "renewed", "renovado")):
            return "refurbished"
        if any(w in lower for w in ("usado", "used", "pre-owned", "segunda mano")):
            return "used"
        return "new"

