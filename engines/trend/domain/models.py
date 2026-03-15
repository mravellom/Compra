from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from .enums import TrendDirection, TrendStrength, TrendType


class TrendSignal(BaseModel):
    """Output of a single trend detection strategy."""
    product_id: int
    direction: TrendDirection = TrendDirection.STABLE
    strength: TrendStrength = TrendStrength.WEAK
    score: float = Field(default=0, ge=0, le=100)
    velocity_ratio: float = 0.0   # 7d vs 30d velocity
    price_momentum: float = 0.0   # price rate of change
    volume_change: float = 0.0    # listing count delta
    metadata: dict = Field(default_factory=dict)


class TrendSnapshot(BaseModel):
    """Point-in-time product state for trend computation."""
    product_id: int
    listing_count: int = 0
    avg_price: float = 0.0
    seller_count: int = 0
    marketplace_count: int = 0
    velocity_7d: float = 0.0
    velocity_30d: float = 0.0
    snapshot_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())


class ProductTrendResult(BaseModel):
    """Stored trend analysis result."""
    id: Optional[int] = None
    product_id: int
    trend_score: float = 0
    velocity_ratio: float = 0
    price_momentum: float = 0
    volume_change: float = 0
    trend_type: str = TrendType.STABLE.value
    trend_strength: str = TrendStrength.WEAK.value
    signals: dict = Field(default_factory=dict)
    detected_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
