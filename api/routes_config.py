"""
Multi-country arbitrage route configuration.

Defines valid arbitrage routes, marketplace fees, and cross-border costs.
All shipping/import data is centralized here for easy tuning.
"""

# ── Marketplace definitions ──────────────────────────────────
MARKETPLACE_FEES: dict[str, dict] = {
    "amazon": {
        "commission": 0.15,
        "payment_processing": 0.0,
        "payment_fixed_fee": 0.0,
        "vat_rate": 0.16,        # Mexico IVA 16%
        "domestic_shipping": 0.0,
        "currency": "MXN",
        "country": "MX",
        "label": "Amazon MX",
    },
    "amazon_us": {
        "commission": 0.15,
        "payment_processing": 0.0,
        "payment_fixed_fee": 0.0,
        "vat_rate": 0.0,         # US: no VAT
        "domestic_shipping": 0.0,
        "currency": "USD",
        "country": "US",
        "label": "Amazon US",
    },
    "mercadolibre_mx": {
        "commission": 0.16,
        "payment_processing": 0.036,
        "payment_fixed_fee": 5.0,  # MXN
        "vat_rate": 0.16,        # Mexico IVA 16%
        "domestic_shipping": 0.0,
        "currency": "MXN",
        "country": "MX",
        "label": "MercadoLibre MX",
    },
    "mercadolibre_ar": {
        "commission": 0.13,
        "payment_processing": 0.036,
        "payment_fixed_fee": 450.0,  # ARS
        "vat_rate": 0.21,        # Argentina IVA 21%
        "domestic_shipping": 0.0,
        "currency": "ARS",
        "country": "AR",
        "label": "MercadoLibre AR",
    },
    "mercadolibre_cl": {
        "commission": 0.13,
        "payment_processing": 0.036,
        "payment_fixed_fee": 250.0,  # CLP
        "vat_rate": 0.19,        # Chile IVA 19%
        "domestic_shipping": 0.0,
        "currency": "CLP",
        "country": "CL",
        "label": "MercadoLibre CL",
    },
    "ebay": {
        "commission": 0.1312,
        "payment_processing": 0.0,
        "payment_fixed_fee": 0.0,
        "vat_rate": 0.0,         # US: no VAT
        "domestic_shipping": 8.0,
        "currency": "USD",
        "country": "US",
        "label": "eBay",
    },
    "aliexpress": {
        "commission": 0.08,
        "payment_processing": 0.0,
        "payment_fixed_fee": 0.0,
        "vat_rate": 0.0,         # No VAT on buy side from CN
        "domestic_shipping": 0.0,
        "currency": "USD",       # AliExpress prices typically in USD
        "country": "CN",
        "label": "AliExpress",
    },
    "mercadolibre_co": {
        "commission": 0.14,
        "payment_processing": 0.036,
        "payment_fixed_fee": 1200.0,  # COP
        "vat_rate": 0.19,        # Colombia IVA 19%
        "domestic_shipping": 0.0,
        "currency": "COP",
        "country": "CO",
        "label": "MercadoLibre CO",
    },
}

# ── Cross-border fees: (buy_country, sell_country) → costs ───
CROSS_BORDER_FEES: dict[tuple[str, str], dict] = {
    # US routes
    ("US", "MX"): {"import_tax_rate": 0.16, "shipping_usd": 15.0, "insurance_rate": 0.02},
    ("US", "CL"): {"import_tax_rate": 0.19, "shipping_usd": 22.0, "insurance_rate": 0.02},
    ("US", "AR"): {"import_tax_rate": 0.50, "shipping_usd": 25.0, "insurance_rate": 0.02},
    # MX routes
    ("MX", "CL"): {"import_tax_rate": 0.19, "shipping_usd": 12.0, "insurance_rate": 0.02},
    ("MX", "AR"): {"import_tax_rate": 0.50, "shipping_usd": 14.0, "insurance_rate": 0.02},
    ("MX", "US"): {"import_tax_rate": 0.0,  "shipping_usd": 15.0, "insurance_rate": 0.02},
    # CN routes
    ("CN", "MX"): {"import_tax_rate": 0.16, "shipping_usd": 8.0,  "insurance_rate": 0.02},
    ("CN", "CL"): {"import_tax_rate": 0.19, "shipping_usd": 12.0, "insurance_rate": 0.02},
    ("CN", "AR"): {"import_tax_rate": 0.50, "shipping_usd": 15.0, "insurance_rate": 0.02},
    ("CN", "US"): {"import_tax_rate": 0.0,  "shipping_usd": 10.0, "insurance_rate": 0.02},
    # AR routes
    ("AR", "MX"): {"import_tax_rate": 0.16, "shipping_usd": 45.0, "insurance_rate": 0.02},
    ("AR", "US"): {"import_tax_rate": 0.0,  "shipping_usd": 50.0, "insurance_rate": 0.02},
    # CL routes
    ("CL", "MX"): {"import_tax_rate": 0.16, "shipping_usd": 18.0, "insurance_rate": 0.02},
    ("CL", "US"): {"import_tax_rate": 0.0,  "shipping_usd": 25.0, "insurance_rate": 0.02},
    # CO routes
    ("US", "CO"): {"import_tax_rate": 0.19, "shipping_usd": 22.0, "insurance_rate": 0.02},
    ("MX", "CO"): {"import_tax_rate": 0.19, "shipping_usd": 15.0, "insurance_rate": 0.02},
    ("CN", "CO"): {"import_tax_rate": 0.19, "shipping_usd": 14.0, "insurance_rate": 0.02},
    ("CO", "MX"): {"import_tax_rate": 0.16, "shipping_usd": 15.0, "insurance_rate": 0.02},
    ("CO", "US"): {"import_tax_rate": 0.0,  "shipping_usd": 22.0, "insurance_rate": 0.02},
}

# Conservative default for unknown country pairs
DEFAULT_CROSS_BORDER = {"import_tax_rate": 0.20, "shipping_usd": 40.0, "insurance_rate": 0.02}

# ── Valid arbitrage routes ───────────────────────────────────
# Only these (buy_marketplace, sell_marketplace) pairs will be evaluated.
# Format: (buy_mp, sell_mp, route_label, route_difficulty)
# route_difficulty: 1=easy (domestic/near), 2=medium, 3=hard (long distance, high import)
ARBITRAGE_ROUTES: list[tuple[str, str, str, int]] = [
    # US → Latin America
    ("amazon_us", "mercadolibre_mx", "US→MX", 1),
    ("amazon_us", "mercadolibre_cl", "US→CL", 2),
    ("amazon_us", "mercadolibre_ar", "US→AR", 3),
    ("ebay", "mercadolibre_mx", "US→MX", 1),
    ("ebay", "mercadolibre_cl", "US→CL", 2),
    ("ebay", "mercadolibre_ar", "US→AR", 3),
    # MX domestic & outbound
    ("amazon", "mercadolibre_mx", "MX→MX", 1),
    ("mercadolibre_mx", "amazon", "MX→MX", 1),
    ("amazon", "mercadolibre_cl", "MX→CL", 2),
    ("amazon", "mercadolibre_ar", "MX→AR", 3),
    ("mercadolibre_mx", "mercadolibre_cl", "MX→CL", 2),
    ("mercadolibre_mx", "mercadolibre_ar", "MX→AR", 3),
    # CN → Latin America (AliExpress cheap source)
    ("aliexpress", "mercadolibre_mx", "CN→MX", 2),
    ("aliexpress", "mercadolibre_cl", "CN→CL", 2),
    ("aliexpress", "mercadolibre_ar", "CN→AR", 3),
    ("aliexpress", "amazon", "CN→MX", 2),
    # US/CN → CO
    ("amazon_us", "mercadolibre_co", "US→CO", 2),
    ("ebay", "mercadolibre_co", "US→CO", 2),
    ("aliexpress", "mercadolibre_co", "CN→CO", 2),
    ("amazon", "mercadolibre_co", "MX→CO", 2),
    # Cross-LATAM
    ("mercadolibre_ar", "mercadolibre_mx", "AR→MX", 2),
    ("mercadolibre_cl", "mercadolibre_mx", "CL→MX", 2),
    ("mercadolibre_co", "mercadolibre_mx", "CO→MX", 2),
]

# Build a set for fast lookup
VALID_ROUTE_PAIRS: set[tuple[str, str]] = {(r[0], r[1]) for r in ARBITRAGE_ROUTES}

# Build route metadata lookup
ROUTE_META: dict[tuple[str, str], dict] = {
    (r[0], r[1]): {"label": r[2], "difficulty": r[3]}
    for r in ARBITRAGE_ROUTES
}


def get_route_label(buy_mp: str, sell_mp: str) -> str:
    """Get human-readable route label like 'US→MX'."""
    meta = ROUTE_META.get((buy_mp, sell_mp))
    if meta:
        return meta["label"]
    buy_country = MARKETPLACE_FEES.get(buy_mp, {}).get("country", "?")
    sell_country = MARKETPLACE_FEES.get(sell_mp, {}).get("country", "?")
    return f"{buy_country}→{sell_country}"


def get_route_difficulty(buy_mp: str, sell_mp: str) -> int:
    """Get route difficulty (1-3). Higher = harder/riskier."""
    meta = ROUTE_META.get((buy_mp, sell_mp))
    return meta["difficulty"] if meta else 2


def get_cross_border_fees(buy_mp: str, sell_mp: str) -> dict:
    """Get directional cross-border fees based on buy/sell countries."""
    buy_country = MARKETPLACE_FEES.get(buy_mp, {}).get("country", "?")
    sell_country = MARKETPLACE_FEES.get(sell_mp, {}).get("country", "?")
    return CROSS_BORDER_FEES.get((buy_country, sell_country), DEFAULT_CROSS_BORDER)


def is_cross_border(buy_mp: str, sell_mp: str) -> bool:
    """Check if a route crosses country borders."""
    buy_country = MARKETPLACE_FEES.get(buy_mp, {}).get("country", "?")
    sell_country = MARKETPLACE_FEES.get(sell_mp, {}).get("country", "?")
    return buy_country != sell_country
