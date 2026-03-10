"""
Product discovery & trending API routes.
"""
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, Query

from ..database import get_db
from ..models import MasterProduct, ProductListing

router = APIRouter(prefix="/discovery", tags=["discovery"])


@router.get("/trending")
async def get_trending(
    limit: int = Query(20, ge=1, le=100),
    category: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get trending products sorted by trend_score."""
    query = (
        select(
            MasterProduct.id,
            MasterProduct.canonical_name,
            MasterProduct.brand,
            MasterProduct.category,
            MasterProduct.avg_price,
            MasterProduct.listing_count,
            MasterProduct.marketplace_count,
            MasterProduct.seller_count,
            MasterProduct.trend_score,
            MasterProduct.trend_label,
            MasterProduct.velocity_7d,
            MasterProduct.velocity_30d,
            MasterProduct.first_seen_at,
        )
        .where(MasterProduct.is_trending.is_(True))
        .order_by(MasterProduct.trend_score.desc())
    )

    if category:
        query = query.where(MasterProduct.category == category)

    query = query.limit(limit)
    result = await db.execute(query)

    return [
        {
            "product_id": row.id,
            "product_name": row.canonical_name,
            "brand": row.brand,
            "category": row.category,
            "avg_price": float(row.avg_price) if row.avg_price else 0,
            "listing_count": row.listing_count or 0,
            "marketplace_count": row.marketplace_count or 0,
            "seller_count": row.seller_count or 0,
            "trend_score": row.trend_score or 0,
            "trend_label": row.trend_label or "stable",
            "velocity_7d": row.velocity_7d or 0,
            "velocity_30d": row.velocity_30d or 0,
            "first_seen_at": row.first_seen_at,
        }
        for row in result.all()
    ]


@router.get("/new")
async def get_new_products(
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Get recently discovered products."""
    query = (
        select(
            MasterProduct.id,
            MasterProduct.canonical_name,
            MasterProduct.brand,
            MasterProduct.category,
            MasterProduct.avg_price,
            MasterProduct.listing_count,
            MasterProduct.marketplace_count,
            MasterProduct.trend_score,
            MasterProduct.first_seen_at,
        )
        .where(
            MasterProduct.first_seen_at >= func.now() - text(f"INTERVAL '{days} days'")
        )
        .order_by(MasterProduct.first_seen_at.desc())
        .limit(limit)
    )

    result = await db.execute(query)

    return [
        {
            "product_id": row.id,
            "product_name": row.canonical_name,
            "brand": row.brand,
            "category": row.category,
            "avg_price": float(row.avg_price) if row.avg_price else 0,
            "listing_count": row.listing_count or 0,
            "marketplace_count": row.marketplace_count or 0,
            "trend_score": row.trend_score or 0,
            "first_seen_at": row.first_seen_at,
        }
        for row in result.all()
    ]


@router.get("/stats")
async def discovery_stats(db: AsyncSession = Depends(get_db)):
    """Summary stats for the discovery dashboard."""
    result = await db.execute(
        select(
            func.count(MasterProduct.id).label("total_products"),
            func.count(MasterProduct.id).filter(
                MasterProduct.is_trending.is_(True)
            ).label("trending_count"),
            func.count(MasterProduct.id).filter(
                MasterProduct.first_seen_at >= func.now() - text("INTERVAL '7 days'")
            ).label("new_7d"),
            func.avg(MasterProduct.listing_count).label("avg_listings"),
            func.avg(MasterProduct.marketplace_count).label("avg_marketplaces"),
        )
    )
    row = result.one()

    # Top categories by product count
    cat_result = await db.execute(
        select(
            MasterProduct.category,
            func.count(MasterProduct.id).label("count"),
            func.avg(MasterProduct.trend_score).label("avg_trend"),
        )
        .where(MasterProduct.category.isnot(None))
        .group_by(MasterProduct.category)
        .order_by(func.count(MasterProduct.id).desc())
        .limit(10)
    )

    return {
        "total_products": row.total_products,
        "trending_count": row.trending_count,
        "new_last_7d": row.new_7d,
        "avg_listings_per_product": round(float(row.avg_listings or 0), 1),
        "avg_marketplaces_per_product": round(float(row.avg_marketplaces or 0), 1),
        "top_categories": [
            {
                "category": r.category,
                "product_count": r.count,
                "avg_trend_score": round(float(r.avg_trend or 0), 1),
            }
            for r in cat_result.all()
        ],
    }


@router.get("/product/{product_id}/history")
async def product_history(
    product_id: int,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Get snapshot history for a specific product."""
    result = await db.execute(
        text("""
            SELECT snapshot_date, listing_count, marketplace_count,
                   seller_count, avg_price, min_price, max_price,
                   total_reviews, total_sales
            FROM product_snapshots
            WHERE master_product_id = :pid
              AND snapshot_date >= CURRENT_DATE - :days * INTERVAL '1 day'
            ORDER BY snapshot_date ASC
        """),
        {"pid": product_id, "days": days},
    )

    return [
        {
            "date": str(row.snapshot_date),
            "listing_count": row.listing_count,
            "marketplace_count": row.marketplace_count,
            "seller_count": row.seller_count,
            "avg_price": float(row.avg_price),
            "min_price": float(row.min_price),
            "max_price": float(row.max_price),
            "total_reviews": row.total_reviews,
            "total_sales": row.total_sales,
        }
        for row in result.all()
    ]
