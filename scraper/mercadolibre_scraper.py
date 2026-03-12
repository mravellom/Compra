import asyncio
import logging
import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .price_parser import extract_ml_price, validate_price
from .schemas import RawListing
from .stealth import StealthSession, ProxyPool
from .rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Configuración por país
ML_SITES = {
    "mercadolibre_ar": {
        "url": "https://listado.mercadolibre.com.ar/{query}",
        "currency": "ARS",
        "lang": "es-AR,es;q=0.9,en;q=0.8",
        "needs_js": False,
    },
    "mercadolibre_mx": {
        "url": "https://listado.mercadolibre.com.mx/{query}",
        "currency": "MXN",
        "lang": "es-MX,es;q=0.9,en;q=0.8",
        "needs_js": False,
    },
    "mercadolibre_cl": {
        "url": "https://listado.mercadolibre.cl/{query}",
        "currency": "CLP",
        "lang": "es-CL,es;q=0.9,en;q=0.8",
        "needs_js": True,
        "locale": "es-CL",
        "timezone": "America/Santiago",
    },
    "mercadolibre_co": {
        "url": "https://listado.mercadolibre.com.co/{query}",
        "currency": "COP",
        "lang": "es-CO,es;q=0.9,en;q=0.8",
        "needs_js": True,
        "locale": "es-CO",
        "timezone": "America/Bogota",
    },
}

# Sites that serve a JS bot challenge requiring a real browser
_JS_SITES = {sid for sid, cfg in ML_SITES.items() if cfg.get("needs_js")}


class MercadoLibreScraper:
    def __init__(
        self,
        site_id: str = "mercadolibre_ar",
        rate_limiter: RateLimiter | None = None,
        proxy_pool: ProxyPool | None = None,
    ):
        config = ML_SITES[site_id]
        self.marketplace_id = site_id
        self._search_url = config["url"]
        self._currency = config["currency"]
        self._lang = config["lang"]
        self._needs_js = config.get("needs_js", False)
        self._locale = config.get("locale", "es-MX")
        self._timezone = config.get("timezone", "America/Mexico_City")
        self._rate_limiter = rate_limiter
        self._proxy_pool = proxy_pool

    async def scrape(self, search_term: str, max_results: int = 20) -> list[RawListing]:
        if self._needs_js:
            return await self._scrape_with_playwright(search_term, max_results)
        return await self._scrape_with_httpx(search_term, max_results)

    # ── httpx path (AR, MX) ──────────────────────────────────
    async def _scrape_with_httpx(self, search_term: str, max_results: int) -> list[RawListing]:
        listings: list[RawListing] = []
        query = quote_plus(search_term).replace("+", "-")
        url = self._search_url.format(query=query)
        logger.info("Fetching %s", url)

        try:
            async with StealthSession(
                proxy_pool=self._proxy_pool,
                rate_limiter=self._rate_limiter,
                accept_language=self._lang,
                base_delay=2.0,
                skip_accept_encoding=True,
            ) as session:
                resp = await session.fetch(url, domain=self.marketplace_id)
        except Exception:
            logger.error("Error fetching %s for '%s'", self.marketplace_id, search_term, exc_info=True)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select(".ui-search-layout__item")

        for item in items[:max_results]:
            try:
                listing = self._parse_item(item)
                if listing:
                    listings.append(listing)
            except Exception:
                logger.debug("Skipping ML item, parse error", exc_info=True)

        logger.info("Scraped %d listings for '%s'", len(listings), search_term)
        return listings

    # ── Playwright path (CL, CO) ─────────────────────────────
    async def _scrape_with_playwright(self, search_term: str, max_results: int) -> list[RawListing]:
        from .browser import get_shared_browser

        listings: list[RawListing] = []
        query = quote_plus(search_term).replace("+", "-")
        url = self._search_url.format(query=query)
        logger.info("[Playwright] Fetching %s", url)

        browser_mgr = await get_shared_browser()
        page = await browser_mgr.new_page(
            locale=self._locale,
            timezone_id=self._timezone,
        )

        try:
            # Navigate and wait for the bot challenge to resolve
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Wait for the real content — the challenge page auto-reloads
            # after solving the SHA-256 verification
            try:
                await page.wait_for_selector(
                    ".ui-search-layout__item",
                    timeout=20000,
                )
            except Exception:
                # Check if we're still on a challenge page
                content = await page.content()
                if len(content) < 5000 and "_bmstate" in content:
                    logger.warning(
                        "[%s] Bot challenge not resolved after 20s, retrying...",
                        self.marketplace_id,
                    )
                    # Give it more time — the challenge JS needs to run
                    await asyncio.sleep(5)
                    try:
                        await page.wait_for_selector(
                            ".ui-search-layout__item",
                            timeout=15000,
                        )
                    except Exception:
                        logger.warning(
                            "[%s] Could not bypass bot challenge for '%s'",
                            self.marketplace_id, search_term,
                        )
                        return []
                else:
                    logger.warning(
                        "[%s] No search results found for '%s' (page length: %d)",
                        self.marketplace_id, search_term, len(content),
                    )
                    return []

            # Get the fully rendered HTML
            html = await page.content()

        except Exception:
            logger.error(
                "[Playwright] Error fetching %s for '%s'",
                self.marketplace_id, search_term, exc_info=True,
            )
            return []
        finally:
            await page.close()

        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(".ui-search-layout__item")

        for item in items[:max_results]:
            try:
                listing = self._parse_item(item)
                if listing:
                    listings.append(listing)
            except Exception:
                logger.debug("Skipping ML item, parse error", exc_info=True)

        logger.info("[Playwright] Scraped %d listings for '%s'", len(listings), search_term)
        return listings

    # ── Parsing (shared by both paths) ───────────────────────
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

        # Reviews count
        reviews_count = 0
        reviews_el = item.select_one(".poly-reviews__total")
        if reviews_el:
            reviews_text = re.sub(r"[^\d]", "", reviews_el.get_text(strip=True))
            reviews_count = int(reviews_text) if reviews_text else 0

        # Star rating
        seller_rating = None
        rating_el = item.select_one(".poly-reviews__rating")
        if rating_el:
            m = re.search(r"([\d.]+)", rating_el.get_text(strip=True))
            if m:
                seller_rating = float(m.group(1))

        # Free shipping
        is_free_shipping = bool(
            item.select_one(".poly-component__shipping .poly-component__shipped-text")
            or item.select_one("[class*='free-shipping']")
        )

        # Condition from title
        condition = self._detect_condition(title)

        # Sales count from "vendidos" text
        sales_count = 0
        sold_el = item.select_one(".poly-component__sold")
        if sold_el:
            sold_text = re.sub(r"[^\d]", "", sold_el.get_text(strip=True))
            sales_count = int(sold_text) if sold_text else 0

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
        )

    @staticmethod
    def _detect_condition(title: str) -> str:
        lower = title.lower()
        if any(w in lower for w in ("reacondicionado", "refurbished", "renewed", "renovado")):
            return "refurbished"
        if any(w in lower for w in ("usado", "used", "pre-owned", "segunda mano")):
            return "used"
        return "new"
