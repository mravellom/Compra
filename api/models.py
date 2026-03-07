from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
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

    master_product: Mapped["MasterProduct"] = relationship(back_populates="listings")


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

    master_product: Mapped["MasterProduct"] = relationship(back_populates="opportunities")
    buy_listing: Mapped["ProductListing"] = relationship(foreign_keys=[buy_listing_id])
    sell_listing: Mapped["ProductListing"] = relationship(foreign_keys=[sell_listing_id])


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
