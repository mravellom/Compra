"""Amazon parser — extracts listings from Amazon search/category pages.

Supports Amazon MX and US variants.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from scraper_v2.domain.enums import Marketplace
from scraper_v2.domain.models import RawListing

from .base import MarketplaceParser

logger = logging.getLogger(__name__)


def _extract_price(item: Tag) -> float | None:
    """Extract price from Amazon's various price formats."""
    # Primary: screen-reader price
    offscreen = item.select_one(".a-price .a-offscreen")
    if offscreen:
        text = offscreen.get_text(strip=True)
        text = re.sub(r"[^\d.,]", "", text)
        text = text.replace(",", "")
        try:
            return float(text)
        except ValueError:
            pass

    # Fallback: split whole/fraction
    whole = item.select_one(".a-price-whole")
    fraction = item.select_one(".a-price-fraction")
    if whole:
        w = whole.get_text(strip=True).rstrip(".").replace(",", "")
        f = fraction.get_text(strip=True) if fraction else "00"
        try:
            return float(f"{w}.{f}")
        except ValueError:
            pass

    return None


class AmazonParser(MarketplaceParser):
    """Parser for Amazon MX/US search and category pages."""

    def __init__(self, marketplace: Marketplace) -> None:
        self._marketplace = marketplace
        self._currency = "MXN" if marketplace == Marketplace.AMAZON_MX else "USD"
        self._base_url = (
            "https://www.amazon.com.mx"
            if marketplace == Marketplace.AMAZON_MX
            else "https://www.amazon.com"
        )

    def parse_listings(self, html: str, source_url: str) -> list[RawListing]:
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select('[data-component-type="s-search-result"]')
        listings: list[RawListing] = []

        for item in items:
            try:
                listing = self._parse_item(item)
                if listing:
                    listings.append(listing)
            except Exception as exc:
                logger.debug("Amazon parse item error: %s", exc)

        return listings

    def _parse_item(self, item: Tag) -> RawListing | None:
        # Skip sponsored/ad results
        if item.select_one('[data-component-type="sp-sponsored-result"]'):
            return None

        asin = item.get("data-asin", "")
        if not asin:
            return None

        # Title
        title_el = item.select_one(
            "h2 a span, .a-size-medium.a-text-normal, .a-size-base-plus"
        )
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            return None

        # URL
        link = item.select_one("h2 a")
        href = link.get("href", "") if link else ""
        url = urljoin(self._base_url, str(href)) if href else f"{self._base_url}/dp/{asin}"

        # Price
        price = _extract_price(item)
        if not price or price <= 0:
            return None

        # Image
        img = item.select_one("img.s-image")
        image_url = img.get("src", "") if img else ""

        # Reviews
        reviews = 0
        reviews_el = item.select_one('[aria-label*="star"] + span .a-size-base, .a-size-base.s-underline-text')
        if reviews_el:
            text = reviews_el.get_text(strip=True).replace(",", "").replace(".", "")
            match = re.search(r"(\d+)", text)
            if match:
                reviews = int(match.group(1))

        # Rating
        rating = 0.0
        rating_el = item.select_one('[aria-label*="star"]')
        if rating_el:
            label = rating_el.get("aria-label", "")
            match = re.search(r"([\d.]+)", label)
            if match:
                rating = float(match.group(1))

        # Free shipping
        free_shipping = bool(
            item.select_one(
                '.a-color-base.a-text-bold:-soup-contains("envío gratis"), '
                '.a-color-base.a-text-bold:-soup-contains("FREE")'
            )
        )

        # Condition
        condition = "new"

        return RawListing(
            title=title,
            price=price,
            currency=self._currency,
            url=url,
            marketplace=self._marketplace,
            image_url=str(image_url),
            condition=condition,
            seller_rating=rating,
            reviews_count=reviews,
            is_free_shipping=free_shipping,
        )

    def extract_next_page_url(self, html: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        next_btn = soup.select_one(
            "a.s-pagination-next:not(.s-pagination-disabled)"
        )
        if next_btn and next_btn.get("href"):
            return urljoin(self._base_url, str(next_btn["href"]))
        return None
