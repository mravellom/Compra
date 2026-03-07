from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# --- Opportunity ---

class OpportunityOut(BaseModel):
    id: int
    product_name: str
    buy_price: float
    sell_price: float
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


# --- Stats ---

class DashboardStats(BaseModel):
    total_opportunities: int
    avg_roi: float
    avg_profit: float
    top_marketplace: Optional[str] = None
    last_scan: Optional[datetime] = None
