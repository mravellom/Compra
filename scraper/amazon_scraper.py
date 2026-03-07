import logging
import re
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from .schemas import RawListing

logger = logging.getLogger(__name__)

AMAZON_SEARCH_URL = "https://www.amazon.com.mx/s?k={query}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
}


class AmazonScraper:
    marketplace_id = "amazon"

    async def scrape(self, search_term: str, max_results: int = 20) -> list[RawListing]:
        listings: list[RawListing] = []
        url = AMAZON_SEARCH_URL.format(query=quote_plus(search_term))
        logger.info("Fetching %s", url)

        try:
            async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except Exception:
            logger.error("Error fetching Amazon for '%s'", search_term, exc_info=True)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select('[data-component-type="s-search-result"]')

        for item in items[:max_results]:
            try:
                listing = self._parse_item(item)
                if listing:
                    listings.append(listing)
            except Exception:
                logger.debug("Skipping Amazon item, parse error", exc_info=True)

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

        # Price
        price_el = item.select_one(".a-price .a-offscreen")
        if not price_el:
            return None
        price_text = price_el.get_text(strip=True)
        price = self._parse_price(price_text)
        if price is None:
            return None

        # URL from ASIN
        asin = item.get("data-asin", "")
        if not asin:
            return None
        url = f"https://www.amazon.com.mx/dp/{asin}"

        # Image
        img_el = item.select_one("img.s-image")
        image_url = None
        if img_el:
            image_url = img_el.get("src") or img_el.get("data-src")

        return RawListing(
            title=title,
            price=price,
            currency="MXN",
            url=url,
            marketplace_id=self.marketplace_id,
            image_url=image_url,
        )

    @staticmethod
    def _parse_price(text: str) -> float | None:
        """Parsea precios como '$5,199.00' o '$12,498.30'."""
        cleaned = re.sub(r"[^\d.]", "", text)
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
