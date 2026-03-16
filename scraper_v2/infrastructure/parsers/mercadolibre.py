"""MercadoLibre parser — extracts listings from ML search/category pages.

Supports AR, MX, CL, CO marketplace variants.
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
    """Extract price from MercadoLibre's split price elements."""
    # New format: andes-money-amount
    fraction = item.select_one(".andes-money-amount__fraction")
    if fraction:
        raw = fraction.get_text(strip=True).replace(".", "").replace(",", "")
        cents_el = item.select_one(".andes-money-amount__cents")
        cents = f".{cents_el.get_text(strip=True)}" if cents_el else ""
        try:
            return float(raw + cents)
        except ValueError:
            pass
    # Legacy format
    price_el = item.select_one(".price__fraction")
    if price_el:
        raw = price_el.get_text(strip=True).replace(".", "").replace(",", "")
        try:
            return float(raw)
        except ValueError:
            pass
    return None


def _extract_reviews(item: Tag) -> int:
    text = ""
    el = item.select_one(".ui-search-reviews__amount")
    if el:
        text = el.get_text(strip=True)
    match = re.search(r"(\d+)", text.replace(".", "").replace(",", ""))
    return int(match.group(1)) if match else 0


def _extract_seller_info(item: Tag) -> tuple[str, float]:
    seller_el = item.select_one(".ui-search-official-store-label")
    name = seller_el.get_text(strip=True) if seller_el else ""
    rating = 0.0
    thermometer = item.select_one("[class*='seller-status']")
    if thermometer:
        classes = " ".join(thermometer.get("class", []))
        if "excellent" in classes or "5" in classes:
            rating = 5.0
        elif "good" in classes or "4" in classes:
            rating = 4.0
        elif "regular" in classes or "3" in classes:
            rating = 3.0
    return name, rating


class MercadoLibreParser(MarketplaceParser):
    """Parser for MercadoLibre AR/MX/CL/CO."""

    def __init__(self, marketplace: Marketplace) -> None:
        self._marketplace = marketplace
        self._currency = {
            Marketplace.MERCADOLIBRE_AR: "ARS",
            Marketplace.MERCADOLIBRE_MX: "MXN",
            Marketplace.MERCADOLIBRE_CL: "CLP",
            Marketplace.MERCADOLIBRE_CO: "COP",
        }.get(marketplace, "USD")

    def parse_listings(self, html: str, source_url: str) -> list[RawListing]:
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(".ui-search-layout__item")
        listings: list[RawListing] = []

        for item in items:
            try:
                listing = self._parse_item(item, source_url)
                if listing:
                    listings.append(listing)
            except Exception as exc:
                logger.debug("ML parse item error: %s", exc)

        return listings

    def _parse_item(self, item: Tag, source_url: str) -> RawListing | None:
        link = item.select_one("a.ui-search-link, a.ui-search-item__group__element")
        if not link:
            return None

        url = link.get("href", "")
        title_el = link.select_one(".ui-search-item__title")
        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
        if not title or not url:
            return None

        price = _extract_price(item)
        if not price or price <= 0:
            return None

        img = item.select_one("img.ui-search-result-image__element")
        image_url = img.get("data-src", img.get("src", "")) if img else ""

        reviews = _extract_reviews(item)
        seller_name, seller_rating = _extract_seller_info(item)

        condition = "new"
        cond_el = item.select_one(".ui-search-item__group--property")
        if cond_el:
            cond_text = cond_el.get_text(strip=True).lower()
            if "usado" in cond_text or "used" in cond_text:
                condition = "used"

        free_shipping = bool(
            item.select_one(".ui-search-item__shipping--free, [class*='free-shipping']")
        )

        sales = 0
        sales_el = item.select_one(".ui-search-item__group--quantity")
        if sales_el:
            m = re.search(r"(\d[\d.]*)", sales_el.get_text().replace(".", ""))
            if m:
                sales = int(m.group(1))

        return RawListing(
            title=title,
            price=price,
            currency=self._currency,
            url=str(url),
            marketplace=self._marketplace,
            image_url=str(image_url),
            condition=condition,
            seller_name=seller_name,
            seller_rating=seller_rating,
            reviews_count=reviews,
            sales_count=sales,
            is_free_shipping=free_shipping,
        )

    def extract_next_page_url(self, html: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        next_link = soup.select_one(
            "a.andes-pagination__link[title='Siguiente'], "
            "a.andes-pagination__link--next"
        )
        if next_link and next_link.get("href"):
            return str(next_link["href"])
        return None
