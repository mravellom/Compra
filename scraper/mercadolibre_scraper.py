import logging
import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .price_parser import extract_ml_price, validate_price
from .schemas import RawListing
from .stealth import StealthSession, ProxyPool
from .rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Configuración por país
ML_SITES = {
    "mercadolibre_ar": {
        "url": "https://listado.mercadolibre.com.ar/{query}",
        "currency": "ARS",
        "lang": "es-AR,es;q=0.9,en;q=0.8",
    },
    "mercadolibre_mx": {
        "url": "https://listado.mercadolibre.com.mx/{query}",
        "currency": "MXN",
        "lang": "es-MX,es;q=0.9,en;q=0.8",
    },
}


class MercadoLibreScraper:
    def __init__(
        self,
        site_id: str = "mercadolibre_ar",
        rate_limiter: RateLimiter | None = None,
        proxy_pool: ProxyPool | None = None,
    ):
        config = ML_SITES[site_id]
        self.marketplace_id = site_id
        self._search_url = config["url"]
        self._currency = config["currency"]
        self._lang = config["lang"]
        self._rate_limiter = rate_limiter
        self._proxy_pool = proxy_pool

    async def scrape(self, search_term: str, max_results: int = 20) -> list[RawListing]:
        listings: list[RawListing] = []
        query = quote_plus(search_term).replace("+", "-")
        url = self._search_url.format(query=query)
        logger.info("Fetching %s", url)

        try:
            async with StealthSession(
                proxy_pool=self._proxy_pool,
                rate_limiter=self._rate_limiter,
                accept_language=self._lang,
                base_delay=2.0,
                skip_accept_encoding=True,
            ) as session:
                resp = await session.fetch(url, domain=self.marketplace_id)
        except Exception:
            logger.error("Error fetching %s for '%s'", self.marketplace_id, search_term, exc_info=True)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select(".ui-search-layout__item")

        for item in items[:max_results]:
            try:
                listing = self._parse_item(item)
                if listing:
                    listings.append(listing)
            except Exception:
                logger.debug("Skipping ML item, parse error", exc_info=True)

        logger.info("Scraped %d listings for '%s'", len(listings), search_term)
        return listings

    def _parse_item(self, item) -> RawListing | None:
        title_el = item.select_one(".poly-component__title")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if not title:
            return None

        # Price — use robust extractor that handles fraction + cents elements
        price = extract_ml_price(item)
        if price is None:
            return None

        if not validate_price(price, self._currency, self.marketplace_id, title):
            return None

        link_el = item.select_one("a[href*='mercadolibre']")
        if not link_el:
            return None
        url = link_el.get("href", "")
        if not url:
            return None

        img_el = item.select_one("img")
        image_url = None
        if img_el:
            image_url = img_el.get("src") or img_el.get("data-src")

        # Reviews count
        reviews_count = 0
        reviews_el = item.select_one(".poly-reviews__total")
        if reviews_el:
            reviews_text = re.sub(r"[^\d]", "", reviews_el.get_text(strip=True))
            reviews_count = int(reviews_text) if reviews_text else 0

        # Star rating
        seller_rating = None
        rating_el = item.select_one(".poly-reviews__rating")
        if rating_el:
            m = re.search(r"([\d.]+)", rating_el.get_text(strip=True))
            if m:
                seller_rating = float(m.group(1))

        # Free shipping
        is_free_shipping = bool(
            item.select_one(".poly-component__shipping .poly-component__shipped-text")
            or item.select_one("[class*='free-shipping']")
        )

        # Condition from title
        condition = self._detect_condition(title)

        # Sales count from "vendidos" text
        sales_count = 0
        sold_el = item.select_one(".poly-component__sold")
        if sold_el:
            sold_text = re.sub(r"[^\d]", "", sold_el.get_text(strip=True))
            sales_count = int(sold_text) if sold_text else 0

        return RawListing(
            title=title,
            price=price,
            currency=self._currency,
            url=url,
            marketplace_id=self.marketplace_id,
            image_url=image_url,
            reviews_count=reviews_count,
            seller_rating=seller_rating,
            is_free_shipping=is_free_shipping,
            condition=condition,
            sales_count=sales_count,
        )

    @staticmethod
    def _detect_condition(title: str) -> str:
        lower = title.lower()
        if any(w in lower for w in ("reacondicionado", "refurbished", "renewed", "renovado")):
            return "refurbished"
        if any(w in lower for w in ("usado", "used", "pre-owned", "segunda mano")):
            return "used"
        return "new"
