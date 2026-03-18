"""
Shared test fixtures — reusable data builders and mocks.

Provides:
  - RawListing factories
  - OpportunityEvent factories
  - Embedding helpers (from test_resolver.py)
  - ScoringInput builders
  - HTML fixture loaders
"""
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from scraper.schemas import RawListing


# ── Embedding Helpers ────────────────────────────────────────


def make_fake_embedding(seed: int, dim: int = 384) -> list[float]:
    """Deterministic normalized embedding."""
    rng = np.random.RandomState(seed)
    vec = rng.randn(dim).astype(np.float32)
    return (vec / np.linalg.norm(vec)).tolist()


def make_similar_embedding(base: list[float], target_sim: float, dim: int = 384) -> list[float]:
    """Create embedding with specific cosine similarity to base."""
    b = np.array(base, dtype=np.float32)
    b = b / np.linalg.norm(b)
    rng = np.random.RandomState(999)
    rand_vec = rng.randn(dim).astype(np.float32)
    orth = rand_vec - np.dot(rand_vec, b) * b
    orth = orth / np.linalg.norm(orth)
    theta = np.arccos(np.clip(target_sim, -1.0, 1.0))
    result = float(np.cos(theta)) * b + float(np.sin(theta)) * orth
    result = result / np.linalg.norm(result)
    return result.tolist()


# ── RawListing Factory ───────────────────────────────────────


def make_listing(
    title: str = "Sony WH-1000XM4 Headphones",
    price: float = 229.99,
    currency: str = "USD",
    marketplace_id: str = "ebay",
    url: str = "https://www.ebay.com/itm/123456",
    condition: str = "new",
    seller_rating: float | None = 4.5,
    reviews_count: int = 100,
    sales_count: int = 50,
    is_free_shipping: bool = False,
    shipping_price: float | None = None,
) -> RawListing:
    return RawListing(
        title=title,
        price=price,
        currency=currency,
        url=url,
        marketplace_id=marketplace_id,
        condition=condition,
        seller_rating=seller_rating,
        reviews_count=reviews_count,
        sales_count=sales_count,
        is_free_shipping=is_free_shipping,
        shipping_price=shipping_price,
    )


# ── OpportunityEvent Factory ─────────────────────────────────


def make_opportunity_event(
    event_type: str = "new_opportunity",
    opportunity_id: int = 1,
    product_name: str = "Sony WH-1000XM4",
    buy_marketplace: str = "amazon",
    sell_marketplace: str = "mercadolibre_mx",
    route: str = "MX→MX",
    buy_price: float = 100.0,
    sell_price: float = 200.0,
    net_profit: float = 45.0,
    roi: float = 0.45,
    opportunity_score: float = 72.0,
    risk_score: float = 30.0,
    confidence_level: str = "high",
    risk_class: str = "prime",
    buy_url: str = "https://amazon.com.mx/dp/B0001",
    sell_url: str = "https://mercadolibre.com.mx/item/123",
    category: str = "Headphones",
):
    from monetization.publisher import OpportunityEvent
    return OpportunityEvent(
        event_type=event_type,
        opportunity_id=opportunity_id,
        product_name=product_name,
        buy_marketplace=buy_marketplace,
        sell_marketplace=sell_marketplace,
        route=route,
        buy_price=buy_price,
        sell_price=sell_price,
        net_profit=net_profit,
        roi=roi,
        opportunity_score=opportunity_score,
        risk_score=risk_score,
        confidence_level=confidence_level,
        risk_class=risk_class,
        buy_url=buy_url,
        sell_url=sell_url,
        category=category,
    )


# ── ScoringInput Factory ─────────────────────────────────────


def make_scoring_input(**overrides):
    from api.scoring import ScoringInput
    defaults = dict(
        net_profit_usd=45.0,
        roi=0.35,
        margin=0.28,
        buy_price_usd=128.0,
        competitor_count=8,
        total_listings=15,
        buy_seller_rating=4.5,
        buy_seller_reviews=120,
        sell_seller_rating=4.2,
        sell_seller_reviews=85,
        total_sales_count=50,
        total_reviews_count=30,
        estimated_daily_sales=2.5,
        market_depth_score=65.0,
        price_stability_score=72.0,
        listing_age_days=30.0,
        is_cross_border=False,
        price_spread_pct=0.15,
        route_difficulty=1,
        soft_penalty=0.0,
        confidence_penalty=0.0,
    )
    defaults.update(overrides)
    return ScoringInput(**defaults)


# ── OpportunitySnapshot Factory ──────────────────────────────


def make_opportunity_snapshot(**overrides):
    from api.opportunity_filters import OpportunitySnapshot
    defaults = dict(
        confidence_score=90.0,
        net_profit_usd=45.0,
        roi=0.35,
        buy_seller_rating=4.5,
        sell_seller_rating=4.3,
        buy_reviews_count=80,
        sell_reviews_count=60,
        estimated_monthly_sales=12.0,
        price_spread_pct=0.05,
        listing_created_at=None,
        adjusted_profit_usd=None,
        adjusted_roi=None,
    )
    defaults.update(overrides)
    return OpportunitySnapshot(**defaults)
