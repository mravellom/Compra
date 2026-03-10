"""
Amazon MX category crawler with pagination.
Reuses parsing logic from amazon_scraper.
Uses StealthSession for anti-blocking protection.
"""
import logging
import re

from bs4 import BeautifulSoup

from .category_config import get_category_url
from .rate_limiter import RateLimiter
from .schemas import RawListing
from .stealth import StealthSession, ProxyPool

logger = logging.getLogger(__name__)


class AmazonCategoryCrawler:
    """Crawls Amazon MX category/browse-node pages with anti-blocking."""

    marketplace_id = "amazon"

    def __init__(
        self,
        rate_limiter: RateLimiter | None = None,
        proxy_pool: ProxyPool | None = None,
    ):
        self._rate_limiter = rate_limiter or RateLimiter(requests_per_second=0.4, burst=2)
        self._proxy_pool = proxy_pool

    async def crawl_category(
        self, category_slug: str, max_pages: int = 3
    ) -> list[RawListing]:
        base_url = get_category_url(self.marketplace_id, category_slug)
        if not base_url:
            logger.warning("No category mapping for '%s' on Amazon", category_slug)
            return []

        all_listings: list[RawListing] = []
        seen_asins: set[str] = set()

        async with StealthSession(
            proxy_pool=self._proxy_pool,
            rate_limiter=self._rate_limiter,
            accept_language="es-MX,es;q=0.9,en;q=0.8",
            base_delay=2.5,  # Slightly slower for Amazon
        ) as session:
            current_url: str | None = base_url

            for page in range(1, max_pages + 1):
                if not current_url:
                    break

                logger.info(
                    "[amazon] Category '%s' page %d/%d: %s",
                    category_slug, page, max_pages, current_url[:100],
                )

                try:
                    resp = await session.fetch(
                        current_url, domain="amazon",
                    )
                except Exception:
                    logger.error(
                        "Failed to fetch page %d of '%s' on Amazon",
                        page, category_slug, exc_info=True,
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
                    "[amazon] Page %d: %d new listings (total: %d)",
                    page, page_count, len(all_listings),
                )

                current_url = self._find_next_page(soup)

        return all_listings

    def _find_next_page(self, soup: BeautifulSoup) -> str | None:
        next_link = soup.select_one("a.s-pagination-next")
        if next_link and next_link.get("href"):
            href = next_link["href"]
            if href.startswith("/"):
                return f"https://www.amazon.com.mx{href}"
            return href
        return None

    def _parse_item(self, item) -> RawListing | None:
        h2 = item.select_one("h2")
        if not h2:
            return None
        title = h2.get_text(strip=True)
        if not title:
            return None

        price_el = item.select_one(".a-price .a-offscreen")
        if not price_el:
            return None
        price_text = price_el.get_text(strip=True)
        price = self._parse_price(price_text)
        if price is None:
            return None

        asin = item.get("data-asin", "")
        if not asin:
            return None
        url = f"https://www.amazon.com.mx/dp/{asin}"

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
        is_free_shipping = bool(item.select_one(".a-color-base.a-text-bold"))

        return RawListing(
            title=title,
            price=price,
            currency="MXN",
            url=url,
            marketplace_id=self.marketplace_id,
            image_url=image_url,
            reviews_count=reviews_count,
            seller_rating=seller_rating,
            condition=condition,
            is_free_shipping=is_free_shipping,
        )

    @staticmethod
    def _detect_condition(title: str) -> str:
        lower = title.lower()
        if any(w in lower for w in ("reacondicionado", "refurbished", "renewed", "renovado")):
            return "refurbished"
        if any(w in lower for w in ("usado", "used", "pre-owned", "segunda mano")):
            return "used"
        return "new"

    @staticmethod
    def _parse_price(text: str) -> float | None:
        cleaned = re.sub(r"[^\d.]", "", text)
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
