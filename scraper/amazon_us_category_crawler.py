"""
Amazon US (amazon.com) category crawler with pagination.
Reuses parsing logic from amazon_us_scraper but crawls category browse nodes.
Uses StealthSession with US-specific headers and cookies.
"""
import logging
import re

from bs4 import BeautifulSoup

from .category_config import get_category_url
from .price_parser import extract_amazon_price, validate_price
from .rate_limiter import RateLimiter
from .schemas import RawListing
from .stealth import StealthSession, ProxyPool

logger = logging.getLogger(__name__)


class AmazonUSCategoryCrawler:
    """Crawls Amazon US category/browse-node pages with anti-blocking."""

    marketplace_id = "amazon_us"

    def __init__(
        self,
        rate_limiter: RateLimiter | None = None,
        proxy_pool: ProxyPool | None = None,
    ):
        self._rate_limiter = rate_limiter or RateLimiter(requests_per_second=0.3, burst=2)
        self._proxy_pool = proxy_pool

    async def crawl_category(
        self, category_slug: str, max_pages: int = 3
    ) -> list[RawListing]:
        base_url = get_category_url(self.marketplace_id, category_slug)
        if not base_url:
            logger.warning("No category mapping for '%s' on Amazon US", category_slug)
            return []

        all_listings: list[RawListing] = []
        seen_asins: set[str] = set()

        async with StealthSession(
            proxy_pool=self._proxy_pool,
            rate_limiter=self._rate_limiter,
            accept_language="en-US,en;q=0.9",
            base_delay=3.0,
            extra_headers={
                "X-Forwarded-For": "72.21.215.1",
            },
            extra_cookies={"lc-main": "en_US", "i18n-prefs": "USD"},
        ) as session:
            current_url: str | None = base_url

            for page in range(1, max_pages + 1):
                if not current_url:
                    break

                logger.info(
                    "[amazon_us] Category '%s' page %d/%d: %s",
                    category_slug, page, max_pages, current_url[:100],
                )

                try:
                    resp = await session.fetch(
                        current_url, domain="amazon_us",
                    )
                except Exception:
                    logger.error(
                        "Failed to fetch page %d of '%s' on Amazon US",
                        page, category_slug, exc_info=True,
                    )
                    break

                # Detect geo-redirect
                final_url = str(resp.url)
                if "amazon.com.mx" in final_url or "amazon.com.br" in final_url:
                    logger.warning(
                        "Amazon US geo-redirected to %s — need US proxy/VPN",
                        final_url[:80],
                    )
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                items = soup.select('[data-component-type="s-search-result"]')

                if not items:
                    logger.info("No items on page %d, stopping pagination", page)
                    break

                page_count = 0
                for item in items:
                    asin = item.get("data-asin", "")
                    if not asin or asin in seen_asins:
                        continue
                    listing = self._parse_item(item)
                    if listing:
                        seen_asins.add(asin)
                        all_listings.append(listing)
                        page_count += 1

                logger.info(
                    "[amazon_us] Page %d: %d new listings (total: %d)",
                    page, page_count, len(all_listings),
                )

                current_url = self._find_next_page(soup)

        return all_listings

    def _find_next_page(self, soup: BeautifulSoup) -> str | None:
        next_link = soup.select_one("a.s-pagination-next")
        if next_link and next_link.get("href"):
            href = next_link["href"]
            if href.startswith("/"):
                return f"https://www.amazon.com{href}"
            return href
        return None

    def _parse_item(self, item) -> RawListing | None:
        h2 = item.select_one("h2")
        if not h2:
            return None
        title = h2.get_text(strip=True)
        if not title:
            return None

        price = extract_amazon_price(item)
        if price is None:
            return None

        if not validate_price(price, "USD", self.marketplace_id, title):
            return None

        asin = item.get("data-asin", "")
        if not asin:
            return None
        url = f"https://www.amazon.com/dp/{asin}"

        img_el = item.select_one("img.s-image")
        image_url = None
        if img_el:
            image_url = img_el.get("src") or img_el.get("data-src")

        reviews_count = 0
        reviews_el = item.select_one(".a-size-base.s-underline-text")
        if reviews_el:
            reviews_text = re.sub(r"[^\d]", "", reviews_el.get_text(strip=True))
            reviews_count = int(reviews_text) if reviews_text else 0

        seller_rating = None
        rating_el = item.select_one(".a-icon-alt")
        if rating_el:
            m = re.search(r"([\d.]+)", rating_el.get_text(strip=True))
            if m:
                seller_rating = float(m.group(1))

        condition = self._detect_condition(title)

        is_free_shipping = bool(
            item.select_one(".a-icon-prime")
            or item.select_one(".a-color-base.a-text-bold")
        )

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
