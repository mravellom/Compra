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
