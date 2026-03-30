"""Trade outcomes and accuracy tracking endpoints."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("/accuracy")
async def trade_accuracy(
    window: str = Query("7d", description="Time window (e.g. 7d, 30d)"),
    db: AsyncSession = Depends(get_db),
):
    """Returns accuracy stats: projected vs actual profit over a time window."""
    # Parse window
    days = 7
    if window.endswith("d"):
        try:
            days = int(window[:-1])
        except ValueError:
            days = 7

    result = await db.execute(
        text("""
            SELECT
                count(*) AS total_trades,
                count(*) FILTER (WHERE status = 'completed') AS completed,
                round(avg(deviation_pct)::numeric, 4) AS avg_deviation_pct,
                round(avg(actual_profit)::numeric, 2) AS avg_actual_profit,
                round(avg(projected_profit)::numeric, 2) AS avg_projected_profit,
                round(
                    CASE WHEN count(*) FILTER (WHERE status = 'completed') > 0
                    THEN (count(*) FILTER (WHERE status = 'completed' AND actual_profit > 0))::numeric
                         / count(*) FILTER (WHERE status = 'completed') * 100
                    ELSE 0 END, 1
                ) AS win_rate_pct
            FROM trade_outcomes
            WHERE created_at > NOW() - make_interval(days := :days)
        """),
        {"days": days},
    )
    row = result.mappings().first()

    return {
        "window_days": days,
        "total_trades": row["total_trades"] if row else 0,
        "completed": row["completed"] if row else 0,
        "avg_deviation_pct": float(row["avg_deviation_pct"]) if row and row["avg_deviation_pct"] else None,
        "avg_actual_profit": float(row["avg_actual_profit"]) if row and row["avg_actual_profit"] else None,
        "avg_projected_profit": float(row["avg_projected_profit"]) if row and row["avg_projected_profit"] else None,
        "win_rate_pct": float(row["win_rate_pct"]) if row and row["win_rate_pct"] else 0,
    }
