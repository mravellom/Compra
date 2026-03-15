from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TrendOut(BaseModel):
    id: Optional[int] = None
    product_id: int
    product_name: Optional[str] = None
    trend_score: float = 0
    velocity_ratio: float = 0
    price_momentum: float = 0
    volume_change: float = 0
    trend_type: str = "stable"
    trend_strength: str = "weak"
    signals: dict = Field(default_factory=dict)
    detected_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TrendListOut(BaseModel):
    trends: list[TrendOut]
    total: int


class TrendScanResult(BaseModel):
    products_analyzed: int
    trends_detected: int
    breakouts: int
