"""
AliExpress scraper with Playwright.

AliExpress is a full SPA — the initial HTML has no product data.
We use Playwright to render the page and extract listings from the
rendered DOM, with a JSON (__NEXT_DATA__) fallback if available.

Search URL: https://www.aliexpress.com/w/wholesale-{query}.html
"""
import asyncio
import json
import logging
import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .price_parser import validate_price
from .schemas import RawListing
from .rate_limiter import RateLimiter
from .stealth import ProxyPool

logger = logging.getLogger(__name__)

ALIEXPRESS_SEARCH_URL = "https://www.aliexpress.com/w/wholesale-{query}.html"


class AliExpressScraper:
    marketplace_id = "aliexpress"

    def __init__(
        self,
        rate_limiter: RateLimiter | None = None,
        proxy_pool: ProxyPool | None = None,
    ):
        self._rate_limiter = rate_limiter
        self._proxy_pool = proxy_pool

    async def scrape(self, search_term: str, max_results: int = 20) -> list[RawListing]:
        query = quote_plus(search_term).replace("+", "-")
        url = ALIEXPRESS_SEARCH_URL.format(query=query)
        logger.info("[Playwright] Fetching %s", url)

        html = await self._fetch_with_playwright(url)
        if not html:
            return []

        # Strategy 1: __NEXT_DATA__ JSON
        listings = self._parse_next_data(html, max_results)

        # Strategy 2: Rendered HTML cards
        if not listings:
            listings = self._parse_html(html, max_results)

        logger.info("Scraped %d AliExpress listings for '%s'", len(listings), search_term)
        return listings

    async def _fetch_with_playwright(self, url: str) -> str | None:
        from .browser import get_shared_browser

        browser_mgr = await get_shared_browser()
        page = await browser_mgr.new_page(
            locale="en-US",
            timezone_id="America/New_York",
        )

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Wait for product cards to render
            try:
                await page.wait_for_selector(
                    '[class*="search-item-card"], [class*="product-snippet"], '
                    '[class*="manhattan--container"], [class*="list--gallery"]',
                    timeout=15000,
                )
            except Exception:
                logger.debug("No card selector found, waiting for page load...")
                await asyncio.sleep(3)

            # Scroll down to trigger lazy loading
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await asyncio.sleep(1.5)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1.5)

            html = await page.content()
            logger.debug("AliExpress page length: %d", len(html))
            return html

        except Exception:
            logger.error("[Playwright] Error fetching AliExpress", exc_info=True)
            return None
        finally:
            await page.close()

    # ── JSON extraction ──────────────────────────────────────
    def _parse_next_data(self, html: str, max_results: int) -> list[RawListing]:
        listings: list[RawListing] = []

        m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not m:
            return []

        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            logger.debug("Failed to parse __NEXT_DATA__ JSON")
            return []

        items = self._extract_items_from_json(data)

        for item in items[:max_results]:
            try:
                listing = self._parse_json_item(item)
                if listing:
                    listings.append(listing)
            except Exception:
                logger.debug("Skipping AliExpress JSON item", exc_info=True)

        return listings

    def _extract_items_from_json(self, data: dict) -> list[dict]:
        items = []

        try:
            props = data.get("props", {}).get("pageProps", {})
            for path_fn in [
                lambda p: p["data"]["root"]["fields"]["mods"]["itemList"]["content"],
                lambda p: p["data"]["root"]["fields"]["mods"]["itemList"]["items"],
                lambda p: p["initialData"]["data"]["root"]["fields"]["mods"]["itemList"]["content"],
            ]:
                try:
                    items = path_fn(props)
                    if items:
                        return items
                except (KeyError, TypeError):
                    continue
        except Exception:
            pass

        self._deep_find_items(data, items)
        return items

    def _deep_find_items(self, obj, results: list, depth: int = 0) -> None:
        if depth > 8 or len(results) > 50:
            return
        if isinstance(obj, list):
            if len(obj) > 2 and isinstance(obj[0], dict):
                if any(k in obj[0] for k in ("productId", "product_id", "title", "productTitle")):
                    results.extend(obj)
                    return
            for item in obj:
                self._deep_find_items(item, results, depth + 1)
        elif isinstance(obj, dict):
            for v in obj.values():
                self._deep_find_items(v, results, depth + 1)

    def _parse_json_item(self, item: dict) -> RawListing | None:
        title = (
            item.get("title", {}).get("displayTitle", "")
            if isinstance(item.get("title"), dict)
            else item.get("title", "")
            or item.get("productTitle", "")
            or item.get("product_title", "")
        )
        if not title or len(title) < 5:
            return None

        title = re.sub(r"<[^>]+>", "", title).strip()

        price = self._extract_json_price(item)
        if price is None or price <= 0:
            return None

        if not validate_price(price, "USD", self.marketplace_id, title):
            return None

        product_id = str(
            item.get("productId", "")
            or item.get("product_id", "")
            or item.get("id", "")
        )
        product_url = item.get("productDetailUrl", "") or item.get("product_detail_url", "")
        if not product_url and product_id:
            product_url = f"https://www.aliexpress.com/item/{product_id}.html"
        if not product_url:
            return None
        if product_url.startswith("//"):
            product_url = "https:" + product_url

        image_url = (
            item.get("image", {}).get("imgUrl", "")
            if isinstance(item.get("image"), dict)
            else item.get("image", "")
            or item.get("imageUrl", "")
            or item.get("product_image", "")
        )
        if image_url and image_url.startswith("//"):
            image_url = "https:" + image_url
        image_url = image_url or None

        reviews_count = 0
        seller_rating = None
        sales_count = 0

        rating_val = item.get("evaluation", {}).get("starRating", "") if isinstance(item.get("evaluation"), dict) else ""
        if not rating_val:
            rating_val = item.get("starRating", "") or item.get("averageStar", "")
        if rating_val:
            try:
                seller_rating = float(str(rating_val))
            except (ValueError, TypeError):
                pass

        review_val = item.get("evaluation", {}).get("totalCount", 0) if isinstance(item.get("evaluation"), dict) else 0
        if not review_val:
            review_val = item.get("reviews", 0) or item.get("totalReviews", 0)
        reviews_count = int(review_val) if review_val else 0

        orders_text = (
            item.get("trade", {}).get("tradeDesc", "")
            if isinstance(item.get("trade"), dict)
            else item.get("orders", "")
            or item.get("totalOrders", "")
        )
        if orders_text:
            m = re.search(r"([\d,]+)", str(orders_text))
            if m:
                sales_count = int(m.group(1).replace(",", ""))

        shipping_text = ""
        if isinstance(item.get("logistics"), dict):
            shipping_text = item["logistics"].get("logisticsDesc", "")
        elif isinstance(item.get("shipping"), dict):
            shipping_text = item["shipping"].get("text", "")

        is_free_shipping = "free" in str(shipping_text).lower()

        shipping_price = None
        if not is_free_shipping and shipping_text:
            m = re.search(r"\$?([\d.]+)", str(shipping_text))
            if m:
                shipping_price = float(m.group(1))

        return RawListing(
            title=title,
            price=price,
            currency="USD",
            url=product_url,
            marketplace_id=self.marketplace_id,
            image_url=image_url,
            reviews_count=reviews_count,
            seller_rating=seller_rating,
            sales_count=sales_count,
            is_free_shipping=is_free_shipping,
            shipping_price=shipping_price,
            condition="new",
        )

    def _extract_json_price(self, item: dict) -> float | None:
        for path in [
            lambda: item["prices"]["salePrice"]["minPrice"],
            lambda: item["prices"]["salePrice"]["formattedPrice"],
            lambda: item["price"]["minPrice"],
            lambda: item["price"]["salePrice"],
            lambda: item.get("minPrice", {}).get("value"),
            lambda: item.get("salePrice", ""),
            lambda: item.get("price", ""),
        ]:
            try:
                val = path()
                if val:
                    return self._parse_price_value(val)
            except (KeyError, TypeError):
                continue
        return None

    @staticmethod
    def _parse_price_value(val) -> float | None:
        if isinstance(val, (int, float)):
            return float(val) if val > 0 else None
        text = str(val)
        text = re.sub(r"[^\d.,]", "", text)
        if not text:
            return None
        text = text.replace(",", "")
        try:
            return float(text)
        except ValueError:
            return None

    # ── HTML extraction (from rendered DOM) ──────────────────
    def _parse_html(self, html: str, max_results: int) -> list[RawListing]:
        listings: list[RawListing] = []
        soup = BeautifulSoup(html, "html.parser")

        # Multiple selector strategies for AliExpress cards
        items = soup.select('[class*="search-item-card"], [class*="product-snippet"]')
        if not items:
            items = soup.select('[class*="manhattan--container"], .list--gallery--C2f2tvm')
        if not items:
            # Try broader card selectors from rendered SPA
            items = soup.select('[class*="card--gallery"], [class*="search-card-item"]')

        for item in items[:max_results]:
            try:
                listing = self._parse_html_item(item)
                if listing:
                    listings.append(listing)
            except Exception:
                logger.debug("Skipping AliExpress HTML item", exc_info=True)

        return listings

    def _parse_html_item(self, item) -> RawListing | None:
        title_el = (
            item.select_one('[class*="title"]')
            or item.select_one("h1")
            or item.select_one("h3")
        )
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if not title or len(title) < 5:
            return None

        price_el = item.select_one('[class*="price"], [class*="Price"]')
        if not price_el:
            return None
        price_text = price_el.get_text(strip=True)
        price = self._parse_price_value(price_text)
        if price is None or price <= 0:
            return None

        if not validate_price(price, "USD", self.marketplace_id, title):
            return None

        link_el = item.select_one("a[href*='aliexpress.com/item']")
        if not link_el:
            link_el = item.select_one("a[href]")
        if not link_el:
            return None
        product_url = link_el.get("href", "")
        if product_url.startswith("//"):
            product_url = "https:" + product_url
        if not product_url.startswith("http"):
            return None

        img_el = item.select_one("img")
        image_url = None
        if img_el:
            image_url = img_el.get("src") or img_el.get("data-src")
            if image_url and image_url.startswith("//"):
                image_url = "https:" + image_url

        sales_count = 0
        orders_el = item.select_one('[class*="trade"], [class*="sold"]')
        if orders_el:
            m = re.search(r"([\d,]+)", orders_el.get_text(strip=True))
            if m:
                sales_count = int(m.group(1).replace(",", ""))

        seller_rating = None
        rating_el = item.select_one('[class*="star"], [class*="rating"]')
        if rating_el:
            m = re.search(r"([\d.]+)", rating_el.get_text(strip=True))
            if m:
                seller_rating = float(m.group(1))

        return RawListing(
            title=title,
            price=price,
            currency="USD",
            url=product_url,
            marketplace_id=self.marketplace_id,
            image_url=image_url,
            sales_count=sales_count,
            seller_rating=seller_rating,
            is_free_shipping=False,
            condition="new",
        )
