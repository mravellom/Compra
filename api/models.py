from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Integer,
    REAL,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class MasterProduct(Base):
    __tablename__ = "master_products"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    brand: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    embedding = mapped_column(Vector(384), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Discovery & trending
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    listing_count: Mapped[int] = mapped_column(Integer, default=0)
    marketplace_count: Mapped[int] = mapped_column(Integer, default=0)
    seller_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_price: Mapped[float | None] = mapped_column(Numeric(12, 2), default=0)
    trend_score: Mapped[float] = mapped_column(REAL, default=0)
    trend_label: Mapped[str] = mapped_column(Text, default="new")
    velocity_7d: Mapped[float] = mapped_column(REAL, default=0)
    velocity_30d: Mapped[float] = mapped_column(REAL, default=0)
    is_trending: Mapped[bool] = mapped_column(Boolean, default=False)
    last_snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    listings: Mapped[list["ProductListing"]] = relationship(back_populates="master_product")
    opportunities: Mapped[list["Opportunity"]] = relationship(back_populates="master_product")


class ProductListing(Base):
    __tablename__ = "product_listings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    master_product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("master_products.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_title: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, default="USD")
    url: Mapped[str] = mapped_column(Text, nullable=False)
    marketplace_id: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text)
    similarity_score: Mapped[float | None] = mapped_column(REAL)
    scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Enhanced metadata
    condition: Mapped[str] = mapped_column(Text, default="new")
    seller_name: Mapped[str | None] = mapped_column(Text)
    seller_rating: Mapped[float | None] = mapped_column(REAL)
    reviews_count: Mapped[int] = mapped_column(Integer, default=0)
    sales_count: Mapped[int] = mapped_column(Integer, default=0)
    stock_available: Mapped[int | None] = mapped_column(Integer)
    is_free_shipping: Mapped[bool] = mapped_column(Boolean, default=False)
    shipping_price: Mapped[float | None] = mapped_column(Numeric(12, 2))

    master_product: Mapped["MasterProduct"] = relationship(back_populates="listings")


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_url: Mapped[str] = mapped_column(Text, nullable=False)
    marketplace_id: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, default="USD")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    master_product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("master_products.id"), nullable=False)
    buy_listing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("product_listings.id"), nullable=False)
    sell_listing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("product_listings.id"), nullable=False)
    buy_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    sell_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    fees: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    shipping_cost: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    net_profit: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    roi: Mapped[float] = mapped_column(REAL, nullable=False)
    buy_marketplace: Mapped[str] = mapped_column(Text, nullable=False)
    sell_marketplace: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Production-grade scoring
    estimated_sell_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    marketplace_fee: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    payment_fee: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    import_tax: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    domestic_shipping: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    international_shipping: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    sales_velocity_score: Mapped[float] = mapped_column(REAL, default=0)
    competition_score: Mapped[float] = mapped_column(REAL, default=0)
    price_stability_score: Mapped[float] = mapped_column(REAL, default=0)
    opportunity_score: Mapped[float] = mapped_column(REAL, default=0)
    confidence_level: Mapped[str] = mapped_column(Text, default="low")
    competitor_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_market_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    lowest_competitor_price: Mapped[float | None] = mapped_column(Numeric(12, 2))

    # Market depth
    market_depth_score: Mapped[float] = mapped_column(REAL, default=0)
    estimated_daily_sales: Mapped[float] = mapped_column(REAL, default=0)
    estimated_monthly_sales: Mapped[float] = mapped_column(REAL, default=0)
    scalability_level: Mapped[str] = mapped_column(Text, default="low")

    # Demand trend
    demand_trend_score: Mapped[float] = mapped_column(REAL, default=50)
    demand_trend_label: Mapped[str] = mapped_column(Text, default="stable")

    # Lifecycle tracking
    decay_rate: Mapped[float] = mapped_column(REAL, default=0)
    lifetime_hours: Mapped[float] = mapped_column(REAL, default=0)
    urgency_score: Mapped[float] = mapped_column(REAL, default=50)
    lifecycle_label: Mapped[str] = mapped_column(Text, default="fresh")

    # Capital efficiency
    capital_required: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    capital_efficiency_score: Mapped[float] = mapped_column(REAL, default=0)
    capital_tier: Mapped[str] = mapped_column(Text, default="medium")
    recommended_quantity: Mapped[int] = mapped_column(Integer, default=1)

    # Route info
    route: Mapped[str] = mapped_column(Text, default="")
    sell_tax: Mapped[float] = mapped_column(Numeric(12, 2), default=0)

    # Scoring system v2
    risk_score: Mapped[float] = mapped_column(REAL, default=50)
    confidence_score: Mapped[float] = mapped_column(REAL, default=50)

    master_product: Mapped["MasterProduct"] = relationship(back_populates="opportunities")
    buy_listing: Mapped["ProductListing"] = relationship(foreign_keys=[buy_listing_id])
    sell_listing: Mapped["ProductListing"] = relationship(foreign_keys=[sell_listing_id])


class OpportunityHistory(Base):
    __tablename__ = "opportunity_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    master_product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("master_products.id"), nullable=False)
    net_profit: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    roi: Mapped[float] = mapped_column(REAL, nullable=False)
    competitor_count: Mapped[int] = mapped_column(Integer, default=0)
    buy_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    sell_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    opportunity_score: Mapped[float] = mapped_column(REAL, default=0)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CategoryArbitrageStats(Base):
    __tablename__ = "category_arbitrage_stats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    category_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    total_products: Mapped[int] = mapped_column(Integer, default=0)
    opportunity_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_profit: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    avg_roi: Mapped[float] = mapped_column(REAL, default=0)
    avg_sales_velocity: Mapped[float] = mapped_column(REAL, default=0)
    avg_competition: Mapped[float] = mapped_column(REAL, default=0)
    avg_demand_trend: Mapped[float] = mapped_column(REAL, default=50)
    best_roi: Mapped[float] = mapped_column(REAL, default=0)
    best_profit: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    category_score: Mapped[float] = mapped_column(REAL, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ============================================================
# Price Prediction Engine
# ============================================================

class PricePrediction(Base):
    __tablename__ = "price_predictions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("master_products.id"), nullable=False)
    model_type: Mapped[str] = mapped_column(Text, default="baseline")
    horizon_days: Mapped[int] = mapped_column(Integer, default=7)
    predicted_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    confidence_lower: Mapped[float | None] = mapped_column(Numeric(12, 2))
    confidence_upper: Mapped[float | None] = mapped_column(Numeric(12, 2))
    mape: Mapped[float | None] = mapped_column(REAL)
    confidence: Mapped[float] = mapped_column(REAL, default=0.5)
    features_used: Mapped[dict | None] = mapped_column(Text)  # JSON string
    status: Mapped[str] = mapped_column(Text, default="computed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ============================================================
# Trend Detection Engine
# ============================================================

class ProductTrend(Base):
    __tablename__ = "product_trends"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("master_products.id"), nullable=False, unique=True)
    trend_score: Mapped[float] = mapped_column(REAL, default=0)
    velocity_ratio: Mapped[float] = mapped_column(REAL, default=0)
    price_momentum: Mapped[float] = mapped_column(REAL, default=0)
    volume_change: Mapped[float] = mapped_column(REAL, default=0)
    trend_type: Mapped[str] = mapped_column(Text, default="stable")
    trend_strength: Mapped[str] = mapped_column(Text, default="weak")
    signals: Mapped[dict | None] = mapped_column(Text)  # JSON string
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ============================================================
# Execution Engine
# ============================================================

class ExecutionOrder(Base):
    __tablename__ = "execution_orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    opportunity_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("opportunities.id"))
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("master_products.id"), nullable=False)
    order_type: Mapped[str] = mapped_column(Text, nullable=False)
    marketplace: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    total_cost: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    estimated_profit: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    status: Mapped[str] = mapped_column(Text, default="draft")
    approval_state: Mapped[str] = mapped_column(Text, default="pending")
    execution_mode: Mapped[str] = mapped_column(Text, default="manual")
    risk_assessment: Mapped[dict | None] = mapped_column(Text)  # JSON string
    error_message: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExecutionLog(Base):
    __tablename__ = "execution_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("execution_orders.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    old_status: Mapped[str | None] = mapped_column(Text)
    new_status: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(Text)  # JSON string
    actor: Mapped[str] = mapped_column(Text, default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ============================================================
# Truth Engine — Trade Outcomes
# ============================================================

class TradeOutcome(Base):
    __tablename__ = "trade_outcomes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("opportunities.id"), nullable=False)
    master_product_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("master_products.id"))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Predicted values
    buy_price_predicted: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    sell_price_predicted: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    estimated_profit: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    expected_roi: Mapped[float] = mapped_column(REAL, default=0)

    # Actual values
    buy_price_actual: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    sell_price_actual: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    actual_profit: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    actual_roi: Mapped[float] = mapped_column(REAL, default=0)

    # Time tracking
    time_to_sell_minutes: Mapped[float | None] = mapped_column(REAL)

    # Status
    sold: Mapped[bool] = mapped_column(Boolean, default=False)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)

    # Route
    buy_marketplace: Mapped[str] = mapped_column(Text, default="")
    sell_marketplace: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AlertConfig(Base):
    __tablename__ = "alert_configs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    min_roi: Mapped[float] = mapped_column(REAL, default=0.20)
    min_profit: Mapped[float] = mapped_column(Numeric(12, 2), default=30.00)
    max_buy_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    categories: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    marketplaces: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    telegram_chat_id: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
