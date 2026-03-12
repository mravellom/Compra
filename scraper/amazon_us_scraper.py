"""
Amazon US scraper (amazon.com).

Reuses the same HTML structure as Amazon MX but targets the US domain,
uses USD currency, and adjusts accept-language headers.
"""
import logging
import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .price_parser import extract_amazon_price, normalize_price, validate_price
from .schemas import RawListing
from .stealth import StealthSession, ProxyPool
from .rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

AMAZON_US_SEARCH_URL = "https://www.amazon.com/s?k={query}"


class AmazonUSScraper:
    marketplace_id = "amazon_us"

    def __init__(
        self,
        rate_limiter: RateLimiter | None = None,
        proxy_pool: ProxyPool | None = None,
    ):
        self._rate_limiter = rate_limiter
        self._proxy_pool = proxy_pool

    async def scrape(self, search_term: str, max_results: int = 20) -> list[RawListing]:
        listings: list[RawListing] = []
        url = AMAZON_US_SEARCH_URL.format(query=quote_plus(search_term))
        logger.info("Fetching %s", url)

        try:
            async with StealthSession(
                proxy_pool=self._proxy_pool,
                rate_limiter=self._rate_limiter,
                accept_language="en-US,en;q=0.9",
                base_delay=3.0,
                extra_headers={
                    # Force US locale to prevent geo-redirect to amazon.com.mx
                    "X-Forwarded-For": "72.21.215.1",
                },
                extra_cookies={"lc-main": "en_US", "i18n-prefs": "USD"},
            ) as session:
                resp = await session.fetch(url, domain="amazon_us")
        except Exception:
            logger.error("Error fetching Amazon US for '%s'", search_term, exc_info=True)
            return []

        # Detect geo-redirect: if we got sent to amazon.com.mx, skip
        final_url = str(resp.url)
        if "amazon.com.mx" in final_url or "amazon.com.br" in final_url:
            logger.warning(
                "Amazon US geo-redirected to %s — skipping (need US proxy/VPN)",
                final_url[:80],
            )
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select('[data-component-type="s-search-result"]')

        for item in items[:max_results]:
            try:
                listing = self._parse_item(item)
                if listing:
                    listings.append(listing)
            except Exception:
                logger.debug("Skipping Amazon US item, parse error", exc_info=True)

        logger.info("Scraped %d listings for '%s'", len(listings), search_term)
        return listings

    def _parse_item(self, item) -> RawListing | None:
        # Title
        h2 = item.select_one("h2")
        if not h2:
            return None
        title = h2.get_text(strip=True)
        if not title:
            return None

        # Price — Amazon US uses the same HTML structure as MX
        price = extract_amazon_price(item)
        if price is None:
            return None

        if not validate_price(price, "USD", self.marketplace_id, title):
            return None

        # URL from ASIN
        asin = item.get("data-asin", "")
        if not asin:
            return None
        url = f"https://www.amazon.com/dp/{asin}"

        # Image
        img_el = item.select_one("img.s-image")
        image_url = None
        if img_el:
            image_url = img_el.get("src") or img_el.get("data-src")

        # Reviews count
        reviews_count = 0
        reviews_el = item.select_one(".a-size-base.s-underline-text")
        if reviews_el:
            reviews_text = re.sub(r"[^\d]", "", reviews_el.get_text(strip=True))
            reviews_count = int(reviews_text) if reviews_text else 0

        # Star rating
        seller_rating = None
        rating_el = item.select_one(".a-icon-alt")
        if rating_el:
            m = re.search(r"([\d.]+)", rating_el.get_text(strip=True))
            if m:
                seller_rating = float(m.group(1))

        # Condition
        condition = self._detect_condition(title)

        # Free shipping (Prime badge or free shipping text)
        is_free_shipping = bool(
            item.select_one(".a-icon-prime")
            or item.select_one(".a-color-base.a-text-bold")
        )

        # Shipping price — Amazon US sometimes shows it below the price
        shipping_price = None
        shipping_el = item.select_one(".a-row.a-size-base .a-color-secondary")
        if shipping_el:
            ship_text = shipping_el.get_text(strip=True).lower()
            if "free" in ship_text:
                is_free_shipping = True
            else:
                m = re.search(r"\$?([\d.]+)", ship_text)
                if m:
                    shipping_price = float(m.group(1))

        return RawListing(
            title=title,
            price=price,
            currency="USD",
            url=url,
            marketplace_id=self.marketplace_id,
            image_url=image_url,
            reviews_count=reviews_count,
            seller_rating=seller_rating,
            condition=condition,
            is_free_shipping=is_free_shipping,
            shipping_price=shipping_price,
        )

    @staticmethod
    def _detect_condition(title: str) -> str:
        lower = title.lower()
        if any(w in lower for w in ("renewed", "refurbished", "recertified")):
            return "refurbished"
        if any(w in lower for w in ("used", "pre-owned", "open box")):
            return "used"
        return "new"
