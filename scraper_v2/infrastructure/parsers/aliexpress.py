"""AliExpress parser — extracts listings from AliExpress pages.

Handles both server-rendered HTML and __NEXT_DATA__ JSON extraction.
"""

from __future__ import annotations

import json
import logging
import re

from bs4 import BeautifulSoup, Tag

from scraper_v2.domain.enums import Marketplace
from scraper_v2.domain.models import RawListing

from .base import MarketplaceParser

logger = logging.getLogger(__name__)


class AliExpressParser(MarketplaceParser):
    """Parser for AliExpress with multi-strategy extraction."""

    def parse_listings(self, html: str, source_url: str) -> list[RawListing]:
        # Strategy 1: __NEXT_DATA__ JSON
        listings = self._parse_next_data(html)
        if listings:
            return listings

        # Strategy 2: HTML card extraction
        listings = self._parse_html_cards(html)
        return listings

    def _parse_next_data(self, html: str) -> list[RawListing]:
        match = re.search(
            r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        if not match:
            return []

        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []

        listings: list[RawListing] = []
        items = self._deep_find_items(data)

        for item in items:
            try:
                title = item.get("title", {}).get("displayTitle", "") or item.get("title", "")
                if isinstance(title, dict):
                    title = title.get("displayTitle", "")

                price_info = item.get("prices", {}) or item.get("price", {})
                price_str = (
                    price_info.get("salePrice", {}).get("minPrice", "")
                    or price_info.get("minPrice", "")
                    or str(price_info.get("price", ""))
                )
                price_clean = re.sub(r"[^\d.]", "", str(price_str))
                if not price_clean:
                    continue
                price = float(price_clean)
                if price <= 0:
                    continue

                product_id = str(item.get("productId", "") or item.get("itemId", ""))
                url = f"https://www.aliexpress.com/item/{product_id}.html" if product_id else ""

                image = item.get("image", {}).get("imgUrl", "") or item.get("imageUrl", "")
                if image and not image.startswith("http"):
                    image = f"https:{image}"

                orders = 0
                orders_str = str(item.get("trade", {}).get("tradeDesc", ""))
                m = re.search(r"(\d+)", orders_str.replace(",", "").replace("+", ""))
                if m:
                    orders = int(m.group(1))

                rating = 0.0
                rating_val = item.get("evaluation", {}).get("starRating", 0)
                if rating_val:
                    rating = float(rating_val)

                shipping_text = str(item.get("logistics", {}).get("logisticsDesc", ""))
                free_shipping = "free" in shipping_text.lower() or "gratis" in shipping_text.lower()

                listings.append(RawListing(
                    title=str(title),
                    price=price,
                    currency="USD",
                    url=url,
                    marketplace=Marketplace.ALIEXPRESS,
                    image_url=str(image),
                    seller_rating=rating,
                    sales_count=orders,
                    is_free_shipping=free_shipping,
                ))
            except Exception as exc:
                logger.debug("AliExpress JSON item parse error: %s", exc)

        return listings

    def _parse_html_cards(self, html: str) -> list[RawListing]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(
            '[class*="product-card"], [class*="search-item"], '
            '[class*="list--gallery"] > div'
        )
        listings: list[RawListing] = []

        for card in cards:
            try:
                link = card.select_one("a[href*='/item/']")
                if not link:
                    continue
                url = str(link.get("href", ""))
                if url.startswith("//"):
                    url = f"https:{url}"

                title = link.get("title", "") or link.get_text(strip=True)

                price_el = card.select_one('[class*="price"]')
                if not price_el:
                    continue
                price_text = re.sub(r"[^\d.]", "", price_el.get_text(strip=True))
                if not price_text:
                    continue
                price = float(price_text)
                if price <= 0:
                    continue

                img = card.select_one("img")
                image_url = img.get("src", "") if img else ""
                if image_url and not str(image_url).startswith("http"):
                    image_url = f"https:{image_url}"

                listings.append(RawListing(
                    title=str(title),
                    price=price,
                    currency="USD",
                    url=url,
                    marketplace=Marketplace.ALIEXPRESS,
                    image_url=str(image_url),
                ))
            except Exception:
                continue

        return listings

    @staticmethod
    def _deep_find_items(data: dict | list, depth: int = 0) -> list[dict]:
        """Recursively find product item arrays in nested JSON."""
        if depth > 10:
            return []
        items: list[dict] = []

        if isinstance(data, dict):
            if "productId" in data or "itemId" in data:
                items.append(data)
            for v in data.values():
                if isinstance(v, (dict, list)):
                    items.extend(AliExpressParser._deep_find_items(v, depth + 1))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    if "productId" in item or "itemId" in item:
                        items.append(item)
                    else:
                        items.extend(AliExpressParser._deep_find_items(item, depth + 1))

        return items

    def extract_next_page_url(self, html: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        next_btn = soup.select_one(
            "a[class*='next'], button[class*='next'] + a"
        )
        if next_btn and next_btn.get("href"):
            href = str(next_btn["href"])
            if href.startswith("//"):
                href = f"https:{href}"
            return href
        return None
