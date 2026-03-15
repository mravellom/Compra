from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PredictionRequest(BaseModel):
    product_id: int
    horizon_days: int = Field(7, description="Forecast horizon: 7, 14, or 30 days")
    model_type: Optional[str] = Field(None, description="Model: baseline, prophet, xgboost. None = auto-select")


class BatchPredictionRequest(BaseModel):
    product_ids: list[int] = Field(default_factory=list, description="Product IDs. Empty = auto-select top products")
    horizon_days: int = 7
    model_type: Optional[str] = None
    limit: int = Field(50, ge=1, le=200)


class PredictionOut(BaseModel):
    id: Optional[int] = None
    product_id: int
    model_type: str
    horizon_days: int
    predicted_price: float
    confidence_lower: Optional[float] = None
    confidence_upper: Optional[float] = None
    mape: Optional[float] = None
    confidence: float = 0.5
    features_used: dict = Field(default_factory=dict)
    status: str = "computed"
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class PredictionListOut(BaseModel):
    predictions: list[PredictionOut]
    total: int
    product_id: Optional[int] = None
