"""
MercadoLibre API Client — real marketplace integration.

Uses the official MercadoLibre API (api.mercadolibre.com) for:
- Placing purchases (POST /orders)
- Creating listings (POST /items)
- Checking order status (GET /orders/{id})
- Getting item details (GET /items/{id})

Requires OAuth2 credentials:
- ML_APP_ID: Application ID
- ML_CLIENT_SECRET: Client secret
- ML_REFRESH_TOKEN: Long-lived refresh token
- ML_SITE_ID: Site (MLA for Argentina, MLM for Mexico, etc.)

Docs: https://developers.mercadolibre.com.ar/es_ar/api-docs
"""
import logging
import os
import re
import time
from typing import Optional

import httpx

from .marketplace_client import (
    MarketplaceClient,
    MarketplaceItemStatus,
    MarketplaceOrderResult,
)

logger = logging.getLogger(__name__)

# ── Configuration ───────────────────────────────────────────
ML_APP_ID = os.getenv("ML_APP_ID", "")
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "")
ML_REFRESH_TOKEN = os.getenv("ML_REFRESH_TOKEN", "")
ML_SITE_ID = os.getenv("ML_SITE_ID", "MLA")  # MLA=Argentina, MLM=Mexico, MLC=Chile, MCO=Colombia

ML_API_BASE = "https://api.mercadolibre.com"
ML_AUTH_URL = f"{ML_API_BASE}/oauth/token"

# Fee structure by site
ML_FEES: dict[str, float] = {
    "MLA": 0.13,   # Argentina: ~13%
    "MLM": 0.16,   # Mexico: ~16%
    "MLC": 0.115,  # Chile: ~11.5%
    "MCO": 0.14,   # Colombia: ~14%
    "MLB": 0.16,   # Brazil: ~16%
    "MLU": 0.115,  # Uruguay: ~11.5%
}

# Item ID pattern from URL
_ML_ITEM_RE = re.compile(r"(ML[A-Z])-?(\d+)")


def _extract_item_id(url_or_id: str) -> str | None:
    """Extract MercadoLibre item ID from URL or direct ID."""
    m = _ML_ITEM_RE.search(url_or_id.replace("-", ""))
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return None


class MercadoLibreClient(MarketplaceClient):
    """Real MercadoLibre API integration."""

    def __init__(
        self,
        app_id: str = ML_APP_ID,
        client_secret: str = ML_CLIENT_SECRET,
        refresh_token: str = ML_REFRESH_TOKEN,
        site_id: str = ML_SITE_ID,
    ):
        self._app_id = app_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._site_id = site_id
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._http: httpx.AsyncClient | None = None
        self._user_id: str | None = None

    @property
    def marketplace_id(self) -> str:
        return f"mercadolibre_{self._site_id.lower()}"

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=ML_API_BASE,
                timeout=30.0,
                headers={"Accept": "application/json"},
            )
        return self._http

    async def _ensure_token(self) -> str:
        """Refresh OAuth2 access token if expired."""
        if self._access_token and time.monotonic() < self._token_expires_at:
            return self._access_token

        if not self._app_id or not self._client_secret or not self._refresh_token:
            raise RuntimeError(
                "MercadoLibre credentials not configured. "
                "Set ML_APP_ID, ML_CLIENT_SECRET, and ML_REFRESH_TOKEN."
            )

        http = await self._get_http()
        resp = await http.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self._app_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        self._access_token = data["access_token"]
        # Refresh token may rotate
        new_refresh = data.get("refresh_token")
        if new_refresh:
            self._refresh_token = new_refresh

        # Token typically expires in 6 hours; refresh 5 min early
        expires_in = data.get("expires_in", 21600)
        self._token_expires_at = time.monotonic() + expires_in - 300

        self._user_id = str(data.get("user_id", ""))

        logger.info("MercadoLibre token refreshed (user_id=%s, expires_in=%ds)", self._user_id, expires_in)
        return self._access_token

    async def _auth_headers(self) -> dict[str, str]:
        token = await self._ensure_token()
        return {"Authorization": f"Bearer {token}"}

    # ── Buy ─────────────────────────────────────────────────

    async def place_buy_order(
        self,
        listing_url: str,
        quantity: int = 1,
        max_price: Optional[float] = None,
    ) -> MarketplaceOrderResult:
        """Place a purchase on MercadoLibre.

        Flow:
        1. Get item details to verify price/stock
        2. If price > max_price, reject
        3. POST /orders to place purchase
        """
        item_id = _extract_item_id(listing_url)
        if not item_id:
            return MarketplaceOrderResult(
                success=False,
                error_message=f"Cannot extract ML item ID from: {listing_url}",
            )

        http = await self._get_http()
        headers = await self._auth_headers()

        # 1. Get current item details
        item_resp = await http.get(f"/items/{item_id}", headers=headers)
        if item_resp.status_code != 200:
            return MarketplaceOrderResult(
                success=False,
                error_message=f"Item not found: {item_id} (status={item_resp.status_code})",
            )

        item = item_resp.json()
        current_price = item.get("price", 0)
        available_qty = item.get("available_quantity", 0)

        # 2. Price guard
        if max_price and current_price > max_price:
            return MarketplaceOrderResult(
                success=False,
                error_message=f"Price increased: ${current_price} > max ${max_price}",
                executed_price=current_price,
            )

        # Stock guard
        if available_qty < quantity:
            return MarketplaceOrderResult(
                success=False,
                error_message=f"Insufficient stock: {available_qty} < {quantity}",
            )

        # 3. Place order via MercadoLibre checkout
        order_payload = {
            "item_id": item_id,
            "quantity": quantity,
        }

        order_resp = await http.post(
            "/orders", json=order_payload, headers=headers,
        )

        if order_resp.status_code in (200, 201):
            order_data = order_resp.json()
            order_id = str(order_data.get("id", ""))
            fee_amount = sum(
                float(t.get("amount", 0))
                for t in order_data.get("order_items", [{}])[0].get("fees", [])
            )
            return MarketplaceOrderResult(
                success=True,
                marketplace_order_id=order_id,
                executed_price=current_price,
                fees=fee_amount,
                status="created",
                metadata={
                    "item_id": item_id,
                    "seller_id": str(item.get("seller_id", "")),
                    "currency": item.get("currency_id", ""),
                    "shipping_mode": item.get("shipping", {}).get("mode", ""),
                },
            )
        else:
            error_body = order_resp.text
            logger.error("ML order failed: status=%d body=%s", order_resp.status_code, error_body[:500])
            return MarketplaceOrderResult(
                success=False,
                error_message=f"Order API error: {order_resp.status_code} - {error_body[:200]}",
            )

    # ── Sell ────────────────────────────────────────────────

    async def place_sell_listing(
        self,
        title: str,
        price: float,
        quantity: int = 1,
        category_id: Optional[str] = None,
        condition: str = "new",
        description: str = "",
        images: Optional[list[str]] = None,
    ) -> MarketplaceOrderResult:
        """Create a new listing on MercadoLibre.

        Uses POST /items to publish.
        """
        http = await self._get_http()
        headers = await self._auth_headers()

        # Build item payload
        item_payload: dict = {
            "title": title[:60],  # ML max title length
            "price": price,
            "currency_id": self._get_currency(),
            "available_quantity": quantity,
            "buying_mode": "buy_it_now",
            "condition": condition,
            "listing_type_id": "gold_special",  # Premium listing
        }

        if category_id:
            item_payload["category_id"] = category_id
        else:
            # Auto-categorize using ML API
            cat_resp = await http.get(
                f"/sites/{self._site_id}/domain_discovery/search",
                params={"q": title[:60]},
                headers=headers,
            )
            if cat_resp.status_code == 200:
                cats = cat_resp.json()
                if cats:
                    item_payload["category_id"] = cats[0].get("category_id", "")

        if description:
            item_payload["description"] = {"plain_text": description}

        if images:
            item_payload["pictures"] = [{"source": url} for url in images[:10]]

        # Publish
        resp = await http.post("/items", json=item_payload, headers=headers)

        if resp.status_code in (200, 201):
            data = resp.json()
            listing_id = data.get("id", "")
            permalink = data.get("permalink", "")
            return MarketplaceOrderResult(
                success=True,
                marketplace_order_id=listing_id,
                executed_price=price,
                fees=price * ML_FEES.get(self._site_id, 0.13),
                status="active",
                metadata={
                    "listing_id": listing_id,
                    "permalink": permalink,
                    "category_id": data.get("category_id", ""),
                },
            )
        else:
            error = resp.text
            logger.error("ML listing failed: %d %s", resp.status_code, error[:500])
            return MarketplaceOrderResult(
                success=False,
                error_message=f"Listing API error: {resp.status_code} - {error[:200]}",
            )

    # ── Status ──────────────────────────────────────────────

    async def get_order_status(
        self,
        marketplace_order_id: str,
    ) -> MarketplaceOrderResult:
        """Get current order status from MercadoLibre."""
        http = await self._get_http()
        headers = await self._auth_headers()

        resp = await http.get(f"/orders/{marketplace_order_id}", headers=headers)
        if resp.status_code != 200:
            return MarketplaceOrderResult(
                success=False,
                marketplace_order_id=marketplace_order_id,
                error_message=f"Order not found: {resp.status_code}",
            )

        data = resp.json()
        status = data.get("status", "unknown")
        total = float(data.get("total_amount", 0))
        fees = sum(
            float(t.get("amount", 0))
            for oi in data.get("order_items", [])
            for t in oi.get("fees", [])
        )

        return MarketplaceOrderResult(
            success=status in ("paid", "confirmed"),
            marketplace_order_id=marketplace_order_id,
            executed_price=total,
            fees=fees,
            status=status,
            metadata={
                "buyer_id": str(data.get("buyer", {}).get("id", "")),
                "shipping_id": str(data.get("shipping", {}).get("id", "")),
                "date_closed": data.get("date_closed", ""),
                "pack_id": str(data.get("pack_id", "")),
            },
        )

    async def get_item_status(
        self,
        listing_url: str,
    ) -> MarketplaceItemStatus:
        """Get current price and availability for a ML listing."""
        item_id = _extract_item_id(listing_url)
        if not item_id:
            return MarketplaceItemStatus(available=False)

        http = await self._get_http()
        # Public endpoint — no auth needed
        resp = await http.get(f"/items/{item_id}")
        if resp.status_code != 200:
            return MarketplaceItemStatus(available=False)

        data = resp.json()
        status = data.get("status", "")
        return MarketplaceItemStatus(
            available=status == "active" and data.get("available_quantity", 0) > 0,
            current_price=data.get("price"),
            stock=data.get("available_quantity", 0),
            seller_name=str(data.get("seller_id", "")),
            condition=data.get("condition", ""),
            listing_id=item_id,
        )

    async def cancel_order(
        self,
        marketplace_order_id: str,
    ) -> bool:
        """Cancel via MercadoLibre mediations API."""
        http = await self._get_http()
        headers = await self._auth_headers()

        # ML uses claims/mediations for cancellation
        resp = await http.post(
            f"/orders/{marketplace_order_id}/cancel",
            headers=headers,
        )
        success = resp.status_code in (200, 201)
        if not success:
            logger.warning("ML cancel failed for order %s: %d", marketplace_order_id, resp.status_code)
        return success

    async def health_check(self) -> bool:
        """Check ML API reachability."""
        try:
            http = await self._get_http()
            resp = await http.get(f"/sites/{self._site_id}")
            return resp.status_code == 200
        except Exception:
            return False

    def _get_currency(self) -> str:
        return {
            "MLA": "ARS", "MLM": "MXN", "MLC": "CLP",
            "MCO": "COP", "MLB": "BRL", "MLU": "UYU",
        }.get(self._site_id, "USD")

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()
