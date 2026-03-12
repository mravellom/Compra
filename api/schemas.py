from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# --- Opportunity ---

class OpportunityOut(BaseModel):
    id: int
    product_name: str
    buy_price: float
    sell_price: float
    estimated_sell_price: Optional[float] = None
    fees: float
    shipping_cost: float
    net_profit: float
    roi: float
    buy_marketplace: str
    sell_marketplace: str
    buy_url: str
    sell_url: str
    image_url: Optional[str] = None
    status: str
    created_at: datetime

    # Route
    route: str = ""
    sell_tax: float = 0

    # Production scoring
    marketplace_fee: float = 0
    payment_fee: float = 0
    import_tax: float = 0
    domestic_shipping: float = 0
    international_shipping: float = 0
    sales_velocity_score: float = 0
    competition_score: float = 0
    price_stability_score: float = 0
    opportunity_score: float = 0
    confidence_level: str = "low"
    competitor_count: int = 0
    avg_market_price: Optional[float] = None
    lowest_competitor_price: Optional[float] = None

    # Market depth
    market_depth_score: float = 0
    estimated_daily_sales: float = 0
    estimated_monthly_sales: float = 0
    scalability_level: str = "low"

    # Demand trend
    demand_trend_score: float = 50
    demand_trend_label: str = "stable"

    # Lifecycle tracking
    decay_rate: float = 0
    lifetime_hours: float = 0
    urgency_score: float = 50
    lifecycle_label: str = "fresh"

    # Capital efficiency
    capital_required: float = 0
    capital_efficiency_score: float = 0
    capital_tier: str = "medium"
    recommended_quantity: int = 1

    # Scoring system v2
    risk_score: float = 50
    confidence_score: float = 50

    model_config = {"from_attributes": True}


class OpportunityFilters(BaseModel):
    min_roi: Optional[float] = Field(None, description="ROI minimo (0.20 = 20%)")
    max_buy_price: Optional[float] = None
    category: Optional[str] = None
    marketplace: Optional[str] = None
    status: str = "active"
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)


# --- Alert Config ---

class AlertConfigCreate(BaseModel):
    user_id: str
    min_roi: float = Field(0.20, ge=0, le=10)
    min_profit: float = Field(30.0, ge=0)
    max_buy_price: Optional[float] = None
    categories: Optional[list[str]] = None
    marketplaces: Optional[list[str]] = None
    telegram_chat_id: Optional[str] = None
    email: Optional[str] = None
    enabled: bool = True


class AlertConfigOut(AlertConfigCreate):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- Opportunity Detail ---

class RelatedListing(BaseModel):
    id: int
    title: str
    price: float
    currency: str
    marketplace_id: str
    url: str
    image_url: Optional[str] = None
    similarity_score: Optional[float] = None
    scraped_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class OpportunityDetail(OpportunityOut):
    category: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    related_listings: list[RelatedListing] = []


# --- Category Arbitrage ---

class CategoryArbitrageOut(BaseModel):
    category_name: str
    total_products: int
    opportunity_count: int
    avg_profit: float
    avg_roi: float
    avg_sales_velocity: float
    avg_competition: float
    avg_demand_trend: float = 50
    best_roi: float
    best_profit: float
    category_score: float
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- Stats ---

class DashboardStats(BaseModel):
    total_opportunities: int
    avg_roi: float
    avg_profit: float
    top_marketplace: Optional[str] = None
    last_scan: Optional[datetime] = None
