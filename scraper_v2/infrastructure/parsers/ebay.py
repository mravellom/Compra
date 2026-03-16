"""eBay parser — extracts listings from eBay search pages."""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from scraper_v2.domain.enums import Marketplace
from scraper_v2.domain.models import RawListing

from .base import MarketplaceParser

logger = logging.getLogger(__name__)


class EbayParser(MarketplaceParser):
    """Parser for eBay search and category pages."""

    def parse_listings(self, html: str, source_url: str) -> list[RawListing]:
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(".s-item")
        listings: list[RawListing] = []

        for item in items:
            try:
                listing = self._parse_item(item)
                if listing:
                    listings.append(listing)
            except Exception as exc:
                logger.debug("eBay parse error: %s", exc)

        return listings

    def _parse_item(self, item: Tag) -> RawListing | None:
        link = item.select_one(".s-item__link")
        if not link:
            return None

        url = link.get("href", "")
        if not url or "itm/" not in str(url):
            return None

        title_el = item.select_one(".s-item__title")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title or title.lower().startswith("shop on ebay"):
            return None

        # Price
        price_el = item.select_one(".s-item__price")
        if not price_el:
            return None
        price_text = price_el.get_text(strip=True)

        # Detect currency
        currency = "USD"
        if "EUR" in price_text or "\u20ac" in price_text:
            currency = "EUR"
        elif "GBP" in price_text or "\u00a3" in price_text:
            currency = "GBP"

        price_clean = re.sub(r"[^\d.]", "", price_text.split(" to ")[0].split(" a ")[0])
        try:
            price = float(price_clean)
        except ValueError:
            return None
        if price <= 0:
            return None

        # Image
        img = item.select_one(".s-item__image-wrapper img")
        image_url = img.get("src", "") if img else ""

        # Condition
        condition = "new"
        cond_el = item.select_one(".SECONDARY_INFO")
        if cond_el:
            cond_text = cond_el.get_text(strip=True).lower()
            if "refurbished" in cond_text:
                condition = "refurbished"
            elif "used" in cond_text or "pre-owned" in cond_text:
                condition = "used"

        # Shipping
        ship_el = item.select_one(".s-item__shipping, .s-item__freeXDays")
        free_shipping = False
        shipping_price = 0.0
        if ship_el:
            ship_text = ship_el.get_text(strip=True).lower()
            if "free" in ship_text or "gratis" in ship_text:
                free_shipping = True
            else:
                match = re.search(r"[\d.]+", ship_text)
                if match:
                    shipping_price = float(match.group())

        # Seller rating
        seller_rating = 0.0
        rating_el = item.select_one(".s-item__seller-info-text")
        if rating_el:
            match = re.search(r"([\d.]+)%", rating_el.get_text())
            if match:
                seller_rating = float(match.group(1)) / 20.0  # Convert % to 0-5

        # Reviews
        reviews = 0
        reviews_el = item.select_one(".s-item__reviews-count span")
        if reviews_el:
            match = re.search(r"(\d+)", reviews_el.get_text().replace(",", ""))
            if match:
                reviews = int(match.group(1))

        return RawListing(
            title=title,
            price=price,
            currency=currency,
            url=str(url),
            marketplace=Marketplace.EBAY,
            image_url=str(image_url),
            condition=condition,
            seller_rating=seller_rating,
            reviews_count=reviews,
            is_free_shipping=free_shipping,
            shipping_price=shipping_price,
        )

    def extract_next_page_url(self, html: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        next_link = soup.select_one(
            "a.pagination__next, a[aria-label='Go to next search page']"
        )
        if next_link and next_link.get("href"):
            return str(next_link["href"])
        return None
