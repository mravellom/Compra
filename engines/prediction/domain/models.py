from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from .enums import ForecastHorizon, ModelType, PredictionStatus


class PricePoint(BaseModel):
    """A single price observation."""
    price: float
    recorded_at: datetime


class PredictionRequest(BaseModel):
    """Input for generating a price prediction."""
    product_id: int
    horizon: ForecastHorizon = ForecastHorizon.DAYS_7
    model_type: Optional[ModelType] = None  # None = auto-select


class PredictionResult(BaseModel):
    """Output of a prediction strategy."""
    product_id: int
    model_type: ModelType
    horizon_days: int
    predicted_price: float
    confidence_lower: Optional[float] = None
    confidence_upper: Optional[float] = None
    mape: Optional[float] = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    features_used: dict = Field(default_factory=dict)
    status: PredictionStatus = PredictionStatus.COMPUTED
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())


class PriceForecast(BaseModel):
    """Stored prediction with DB identity."""
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
