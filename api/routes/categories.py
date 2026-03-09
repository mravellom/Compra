from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, Query

from ..database import get_db
from ..models import CategoryArbitrageStats
from ..category_engine import refresh_category_stats
from ..schemas import CategoryArbitrageOut

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("/arbitrage", response_model=list[CategoryArbitrageOut])
async def list_category_arbitrage(
    min_roi: float | None = Query(None, description="Minimum avg ROI filter (0.40 = 40%)"),
    min_score: float | None = Query(None, description="Minimum category score"),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Ranked list of product categories by arbitrage profitability."""
    query = (
        select(CategoryArbitrageStats)
        .order_by(CategoryArbitrageStats.category_score.desc())
    )

    if min_roi is not None:
        query = query.where(CategoryArbitrageStats.avg_roi >= min_roi)
    if min_score is not None:
        query = query.where(CategoryArbitrageStats.category_score >= min_score)

    query = query.limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("/arbitrage/refresh", response_model=dict)
async def refresh_categories(db: AsyncSession = Depends(get_db)):
    """Recalculate category arbitrage stats from active opportunities."""
    stats = await refresh_category_stats(db)
    return {
        "categories_analyzed": len(stats),
        "top_category": stats[0].category_name if stats else None,
        "top_score": stats[0].category_score if stats else 0,
    }
