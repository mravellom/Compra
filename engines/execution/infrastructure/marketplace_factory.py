"""
Marketplace Client Factory — returns the correct client for each marketplace.

Usage:
    client = get_marketplace_client("mercadolibre_mla")
    result = await client.place_buy_order(url, quantity=1)
"""
import logging
from typing import Optional

from .marketplace_client import MarketplaceClient

logger = logging.getLogger(__name__)

_clients: dict[str, MarketplaceClient] = {}


def get_marketplace_client(marketplace: str) -> Optional[MarketplaceClient]:
    """Get or create a marketplace client for the given marketplace ID.

    Supported:
    - mercadolibre_* (mercadolibre_mla, mercadolibre_mlm, etc.)

    Returns None if marketplace is not supported or not configured.
    """
    key = marketplace.lower().strip()

    if key in _clients:
        return _clients[key]

    if key.startswith("mercadolibre") or key.startswith("mercado_libre"):
        client = _create_mercadolibre_client(key)
        if client:
            _clients[key] = client
            return client

    # Future: amazon, ebay, aliexpress clients
    logger.warning("No marketplace client available for '%s'", marketplace)
    return None


def _create_mercadolibre_client(marketplace_key: str) -> Optional[MarketplaceClient]:
    """Create a MercadoLibre client from env vars."""
    import os

    app_id = os.getenv("ML_APP_ID", "")
    if not app_id:
        logger.info("MercadoLibre client not configured (ML_APP_ID not set)")
        return None

    # Extract site ID from marketplace key
    site_map = {
        "mla": "MLA", "ar": "MLA",
        "mlm": "MLM", "mx": "MLM",
        "mlc": "MLC", "cl": "MLC",
        "mco": "MCO", "co": "MCO",
        "mlb": "MLB", "br": "MLB",
        "mlu": "MLU", "uy": "MLU",
    }

    site_id = os.getenv("ML_SITE_ID", "MLA")
    for suffix, sid in site_map.items():
        if marketplace_key.endswith(suffix):
            site_id = sid
            break

    from .mercadolibre_client import MercadoLibreClient
    return MercadoLibreClient(site_id=site_id)


def list_available_marketplaces() -> list[str]:
    """Return list of configured marketplace IDs."""
    return list(_clients.keys())
