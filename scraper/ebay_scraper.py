import logging
import re
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from .schemas import RawListing

logger = logging.getLogger(__name__)

EBAY_SEARCH_URL = "https://www.ebay.com/sch/i.html?_nkw={query}&_ipg={per_page}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


class EbayScraper:
    marketplace_id = "ebay"

    async def scrape(self, search_term: str, max_results: int = 20) -> list[RawListing]:
        listings: list[RawListing] = []
        url = EBAY_SEARCH_URL.format(
            query=quote_plus(search_term),
            per_page=min(max_results, 60),
        )
        logger.info("Fetching %s", url)

        try:
            async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except Exception:
            logger.error("Error fetching eBay for '%s'", search_term, exc_info=True)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select(".srp-results li.s-item")

        for item in items[:max_results]:
            try:
                listing = self._parse_item(item)
                if listing:
                    listings.append(listing)
            except Exception:
                logger.debug("Skipping eBay item, parse error", exc_info=True)

        logger.info("Scraped %d listings for '%s'", len(listings), search_term)
        return listings

    def _parse_item(self, item) -> RawListing | None:
        # Title
        title_el = item.select_one(".s-item__title")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if not title or title.lower() == "shop on ebay":
            return None

        # Price
        price_el = item.select_one(".s-item__price")
        if not price_el:
            return None
        price_text = price_el.get_text(strip=True)
        price = self._parse_price(price_text)
        if price is None:
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

        return RawListing(
            title=title,
            price=price,
            currency=self._parse_currency(price_text),
            url=url,
            marketplace_id=self.marketplace_id,
            image_url=image_url,
        )

    @staticmethod
    def _parse_price(text: str) -> float | None:
        """Extrae el precio numerico de un string como '$29.99' o '$10.00 to $20.00'."""
        match = re.search(r"[\d,]+\.?\d*", text.replace(",", ""))
        if not match:
            return None
        try:
            return float(match.group())
        except ValueError:
            return None

    @staticmethod
    def _parse_currency(text: str) -> str:
        if "$" in text:
            return "USD"
        if "EUR" in text or "\u20ac" in text:
            return "EUR"
        if "GBP" in text or "\u00a3" in text:
            return "GBP"
        return "USD"
