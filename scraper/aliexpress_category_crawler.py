"""
AliExpress category crawler — httpx-first with Playwright fallback.

Strategy:
1. Fetch via httpx (fast, no browser overhead) — works when AliExpress
   returns server-rendered HTML or __NEXT_DATA__ JSON.
2. If httpx returns no products, fall back to Playwright with enhanced
   stealth (randomized delays, realistic scrolling, cookie pre-warm).
"""
import asyncio
import json
import logging
import random
import re

import httpx
from bs4 import BeautifulSoup

from .category_config import get_category_url
from .price_parser import validate_price
from .rate_limiter import RateLimiter
from .schemas import RawListing
from .stealth import ProxyPool, random_headers as get_random_headers

logger = logging.getLogger(__name__)

# AliExpress API endpoint (returns JSON, no browser needed)
_API_URL = "https://www.aliexpress.com/glosearch/api/product"
_REFERER_BASE = "https://www.aliexpress.com"

# Map category slugs to AliExpress category IDs extracted from URLs
_CATEGORY_IDS: dict[str, str] = {
    "audifonos": "200003545",
    "parlantes": "200003546",
    "celulares": "5090301",
    "accesorios-celulares": "380208",
    "smartwatch": "200362144",
    "tablets": "200216607",
    "almacenamiento": "70803",
    "perifericos": "70802",
    "networking": "200003074",
    "consolas": "200003064",
    "accesorios-gaming": "200003062",
    "camaras-digitales": "200003547",
    "drones": "200003918",
    "herramientas-electricas": "200003206",
    "electrodomesticos": "200003386",
    "relojes": "200362143",
    "hogar-inteligente": "200003499",
    "iluminacion-led": "200003294",
}


class AliExpressCategoryCrawler:
    """Crawls AliExpress using internal search API, with HTML fallback."""

    marketplace_id = "aliexpress"

    def __init__(
        self,
        rate_limiter: RateLimiter | None = None,
        proxy_pool: ProxyPool | None = None,
    ):
        self._rate_limiter = rate_limiter
        self._proxy_pool = proxy_pool

    async def crawl_category(
        self, category_slug: str, max_pages: int = 3
    ) -> list[RawListing]:
        base_url = get_category_url(self.marketplace_id, category_slug)
        if not base_url:
            logger.warning("No category mapping for '%s' on AliExpress", category_slug)
            return []

        all_listings: list[RawListing] = []
        seen_ids: set[str] = set()

        for page in range(1, max_pages + 1):
            logger.info(
                "[aliexpress] Category '%s' page %d/%d",
                category_slug, page, max_pages,
            )

            if self._rate_limiter:
                await self._rate_limiter.acquire("aliexpress")

            # Strategy 1: Internal API (fast, reliable)
            listings = await self._fetch_api(category_slug, page, seen_ids)

            # Strategy 2: httpx HTML fetch
            if not listings:
                page_url = base_url if page == 1 else f"{base_url}?page={page}"
                html = await self._fetch_httpx(page_url)
                if html:
                    listings = self._parse_next_data(html, seen_ids)
                    if not listings:
                        listings = self._parse_html(html, seen_ids)

            # Strategy 3: Playwright fallback (slow but handles JS)
            if not listings:
                page_url = base_url if page == 1 else f"{base_url}?page={page}"
                html = await self._fetch_with_playwright(page_url)
                if html:
                    listings = self._parse_next_data(html, seen_ids)
                    if not listings:
                        listings = self._parse_html(html, seen_ids)

            if not listings:
                logger.info("No new listings on page %d, stopping", page)
                break

            all_listings.extend(listings)
            logger.info(
                "[aliexpress] Page %d: %d new listings (total: %d)",
                page, len(listings), len(all_listings),
            )

            await asyncio.sleep(random.uniform(1.5, 3.0))

        return all_listings

    # ── API fetch ─────────────────────────────────────────────
    async def _fetch_api(
        self, category_slug: str, page: int, seen_ids: set[str]
    ) -> list[RawListing]:
        """Fetch from AliExpress internal search/category API."""
        cat_id = _CATEGORY_IDS.get(category_slug)
        if not cat_id:
            return []

        params = {
            "catId": cat_id,
            "page": str(page),
            "limit": "60",
            "trafficChannel": "main",
            "SortType": "default",
        }
        headers = get_random_headers()
        headers.update({
            "referer": f"{_REFERER_BASE}/category/{cat_id}/",
            "accept": "application/json, text/plain, */*",
            "origin": _REFERER_BASE,
        })

        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=20.0
            ) as client:
                resp = await client.get(_API_URL, params=params, headers=headers)
                if resp.status_code != 200:
                    logger.debug("[aliexpress] API returned %d", resp.status_code)
                    return []

                data = resp.json()
                items = (
                    data.get("data", {}).get("root", {}).get("fields", {})
                    .get("mods", {}).get("itemList", {}).get("content", [])
                )
                if not items:
                    # Try alternative path
                    items = data.get("result", {}).get("resultList", [])
                if not items:
                    self._deep_find_items(data, items := [])

                listings: list[RawListing] = []
                for item in items:
                    try:
                        product_id = str(
                            item.get("productId", "")
                            or item.get("product_id", "")
                            or item.get("id", "")
                        )
                        if product_id and product_id in seen_ids:
                            continue
                        listing = self._parse_json_item(item)
                        if listing:
                            if product_id:
                                seen_ids.add(product_id)
                            listings.append(listing)
                    except Exception:
                        continue

                if listings:
                    logger.info("[aliexpress] API returned %d items for '%s' page %d",
                                len(listings), category_slug, page)
                return listings

        except Exception:
            logger.debug("[aliexpress] API fetch failed", exc_info=True)
            return []

    # ── httpx fetch ───────────────────────────────────────────
    async def _fetch_httpx(self, url: str) -> str | None:
        """Fast HTTP fetch — works when server returns rendered HTML."""
        headers = get_random_headers()
        headers["referer"] = _REFERER_BASE + "/"

        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=20.0
            ) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200 and len(resp.text) > 5000:
                    return resp.text
        except Exception:
            logger.debug("[aliexpress] httpx fetch failed for %s", url[:80])
        return None

    # ── Playwright fetch (enhanced stealth) ───────────────────
    async def _fetch_with_playwright(self, url: str) -> str | None:
        from .browser import get_shared_browser

        browser_mgr = await get_shared_browser()
        page = await browser_mgr.new_page(
            locale="en-US",
            timezone_id="America/New_York",
        )

        try:
            # Pre-warm: visit homepage first to get cookies
            await page.goto(
                "https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=20000
            )
            await asyncio.sleep(random.uniform(1.0, 2.0))

            # Now visit the actual category page
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            try:
                await page.wait_for_selector(
                    '[class*="search-item-card"], [class*="product-snippet"], '
                    '[class*="manhattan--container"], [class*="list--gallery"], '
                    '[class*="card--gallery"], [class*="search-card-item"]',
                    timeout=15000,
                )
            except Exception:
                logger.debug("No card selector found, waiting for page load...")
                await asyncio.sleep(4)

            # Realistic scrolling
            for scroll_pct in [0.3, 0.6, 0.85, 1.0]:
                await page.evaluate(
                    f"window.scrollTo(0, document.body.scrollHeight * {scroll_pct})"
                )
                await asyncio.sleep(random.uniform(0.8, 1.5))

            html = await page.content()
            return html

        except Exception:
            logger.error("[Playwright] Error fetching AliExpress category", exc_info=True)
            return None
        finally:
            try:
                await page.context.close()
            except Exception:
                pass

    # ── JSON extraction ──────────────────────────────────────
    def _parse_next_data(self, html: str, seen_ids: set[str]) -> list[RawListing]:
        listings: list[RawListing] = []

        m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not m:
            return []

        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return []

        items = self._extract_items_from_json(data)

        for item in items:
            try:
                product_id = str(
                    item.get("productId", "")
                    or item.get("product_id", "")
                    or item.get("id", "")
                )
                if product_id and product_id in seen_ids:
                    continue

                listing = self._parse_json_item(item)
                if listing:
                    if product_id:
                        seen_ids.add(product_id)
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
        if depth > 8 or len(results) > 200:
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

    # ── HTML extraction ──────────────────────────────────────
    def _parse_html(self, html: str, seen_ids: set[str]) -> list[RawListing]:
        listings: list[RawListing] = []
        soup = BeautifulSoup(html, "html.parser")

        items = soup.select('[class*="search-item-card"], [class*="product-snippet"]')
        if not items:
            items = soup.select('[class*="manhattan--container"], .list--gallery--C2f2tvm')
        if not items:
            items = soup.select('[class*="card--gallery"], [class*="search-card-item"]')

        for item in items:
            try:
                listing = self._parse_html_item(item)
                if listing:
                    url_str = str(listing.url)
                    if url_str not in seen_ids:
                        seen_ids.add(url_str)
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
