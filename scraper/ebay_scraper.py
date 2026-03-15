"""
eBay scraper — uses StealthSession with proxy rotation and anti-detection.

eBay blocks plain httpx requests with 503 (WAF). This scraper:
1. Uses StealthSession (randomized UA, headers, proxy rotation, retry)
2. Extracts seller metadata (rating, reviews, condition, shipping)
3. Falls back to Playwright if httpx gets consistently blocked

Search URL: https://www.ebay.com/sch/i.html?_nkw={query}&_ipg=60
"""
import logging
import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .price_parser import extract_ebay_price, validate_price
from .rate_limiter import RateLimiter
from .schemas import RawListing
from .stealth import ProxyPool, StealthSession, detect_block

logger = logging.getLogger(__name__)

EBAY_SEARCH_URL = "https://www.ebay.com/sch/i.html?_nkw={query}&_ipg={per_page}"


class EbayScraper:
    marketplace_id = "ebay"

    def __init__(
        self,
        rate_limiter: RateLimiter | None = None,
        proxy_pool: ProxyPool | None = None,
    ):
        self._rate_limiter = rate_limiter
        self._proxy_pool = proxy_pool

    async def scrape(self, search_term: str, max_results: int = 20) -> list[RawListing]:
        url = EBAY_SEARCH_URL.format(
            query=quote_plus(search_term),
            per_page=min(max_results, 60),
        )
        logger.info("Fetching eBay: %s", url)

        # Try httpx with StealthSession first
        html = await self._fetch_stealth(url)

        # Fallback to Playwright if httpx was blocked
        if html is None:
            logger.info("eBay httpx blocked, falling back to Playwright")
            html = await self._fetch_playwright(url)

        if not html:
            logger.warning("eBay: failed to fetch '%s'", search_term)
            return []

        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(".srp-results li.s-item")

        listings: list[RawListing] = []
        for item in items[:max_results]:
            try:
                listing = self._parse_item(item)
                if listing:
                    listings.append(listing)
            except Exception:
                logger.debug("Skipping eBay item, parse error", exc_info=True)

        logger.info("Scraped %d eBay listings for '%s'", len(listings), search_term)
        return listings

    # ── Fetch strategies ──────────────────────────────────────

    async def _fetch_stealth(self, url: str) -> str | None:
        """Fetch with StealthSession: proxy rotation, UA rotation, retry."""
        try:
            async with StealthSession(
                proxy_pool=self._proxy_pool,
                rate_limiter=self._rate_limiter,
                accept_language="en-US,en;q=0.9",
                base_delay=2.5,
                max_retries=3,
                extra_cookies={
                    "ebay": "%5Esbf%3D%23000000",  # locale cookie
                },
            ) as session:
                resp = await session.fetch(url, domain="ebay")
                if resp is None:
                    return None
                if detect_block(resp):
                    logger.warning("eBay block detected on stealth fetch")
                    return None
                if resp.status_code != 200:
                    logger.warning("eBay returned %d", resp.status_code)
                    return None
                return resp.text
        except Exception:
            logger.error("eBay stealth fetch error", exc_info=True)
            return None

    async def _fetch_playwright(self, url: str) -> str | None:
        """Fallback: render with headless browser for JS-heavy pages."""
        try:
            from .browser import get_shared_browser

            browser_mgr = await get_shared_browser()
            page = await browser_mgr.new_page(
                locale="en-US",
                timezone_id="America/New_York",
            )

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # Wait for search results to render
                try:
                    await page.wait_for_selector(
                        ".srp-results .s-item",
                        timeout=10000,
                    )
                except Exception:
                    logger.debug("eBay Playwright: no results selector found")

                html = await page.content()
                if len(html) < 2000:
                    logger.warning("eBay Playwright: page too short (%d bytes)", len(html))
                    return None
                return html
            finally:
                await page.close()

        except Exception:
            logger.error("eBay Playwright fetch error", exc_info=True)
            return None

    # ── Parsing ───────────────────────────────────────────────

    def _parse_item(self, item) -> RawListing | None:
        # Title
        title_el = item.select_one(".s-item__title")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if not title or title.lower() == "shop on ebay":
            return None

        # Price — use robust extractor
        price = extract_ebay_price(item)
        if price is None:
            return None

        currency = self._parse_currency(item)
        if not validate_price(price, currency, self.marketplace_id, title):
            return None

        # URL
        link_el = item.select_one("a.s-item__link")
        url = link_el.get("href", "") if link_el else ""
        if not url:
            return None

        # Image
        img_el = item.select_one(".s-item__image-wrapper img")
        image_url = None
        if img_el:
            image_url = img_el.get("src") or img_el.get("data-src")

        # Seller metadata
        seller_rating = None
        reviews_count = 0
        condition = "new"
        is_free_shipping = False
        shipping_price = None

        # Condition
        condition_el = item.select_one(".SECONDARY_INFO")
        if condition_el:
            cond_text = condition_el.get_text(strip=True).lower()
            if "refurbished" in cond_text:
                condition = "refurbished"
            elif any(w in cond_text for w in ("pre-owned", "used")):
                condition = "used"

        # Seller info (top-rated, reviews)
        seller_el = item.select_one(".s-item__seller-info-text, .s-item__etrs-text")
        if seller_el:
            text = seller_el.get_text(strip=True)
            pct_match = re.search(r"([\d.]+)%", text)
            if pct_match:
                pct = float(pct_match.group(1))
                seller_rating = round(pct / 20, 1)  # 100% → 5.0, 95% → 4.75

        # Shipping
        shipping_el = item.select_one(".s-item__shipping, .s-item__freeXDays")
        if shipping_el:
            ship_text = shipping_el.get_text(strip=True).lower()
            if "free" in ship_text:
                is_free_shipping = True
            else:
                price_match = re.search(r"\$?([\d.]+)", ship_text)
                if price_match:
                    shipping_price = float(price_match.group(1))

        # Reviews count
        reviews_el = item.select_one(".s-item__reviews-count span")
        if reviews_el:
            count_match = re.search(r"([\d,]+)", reviews_el.get_text(strip=True))
            if count_match:
                reviews_count = int(count_match.group(1).replace(",", ""))

        return RawListing(
            title=title,
            price=price,
            currency=currency,
            url=url,
            marketplace_id=self.marketplace_id,
            image_url=image_url,
            seller_rating=seller_rating,
            reviews_count=reviews_count,
            condition=condition,
            is_free_shipping=is_free_shipping,
            shipping_price=shipping_price,
        )

    @staticmethod
    def _parse_currency(item) -> str:
        price_el = item.select_one(".s-item__price")
        if not price_el:
            return "USD"
        text = price_el.get_text(strip=True)
        if "EUR" in text or "\u20ac" in text:
            return "EUR"
        if "GBP" in text or "\u00a3" in text:
            return "GBP"
        return "USD"
