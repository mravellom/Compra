"""
Product Discovery Engine.

Runs periodically to:
1. Snapshot current product metrics (listing_count, marketplaces, prices)
2. Detect newly emerged products (first_seen within N days)
3. Compute listing velocity (growth rate over 7d / 30d windows)
4. Score trending products using composite velocity + breadth signals
"""
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .db import get_pool

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────
TRENDING_VELOCITY_THRESHOLD = 0.5   # min 7d velocity to qualify
TRENDING_MIN_LISTINGS = 3           # need at least N listings
NEW_PRODUCT_WINDOW_DAYS = 7         # "new" if first_seen < N days ago


@dataclass
class ProductSnapshot:
    master_product_id: int
    canonical_name: str
    category: str | None
    listing_count: int
    marketplace_count: int
    seller_count: int
    avg_price: float
    min_price: float
    max_price: float
    total_reviews: int
    total_sales: int


@dataclass
class TrendResult:
    master_product_id: int
    product_name: str
    category: str | None
    avg_price: float
    marketplace_count: int
    listing_count: int
    trend_score: float
    velocity_7d: float
    velocity_30d: float
    is_new: bool
    days_since_discovery: int


async def take_snapshots() -> list[ProductSnapshot]:
    """
    Aggregate current listings per master_product into daily snapshots.
    Upserts into product_snapshots table.
    """
    pool = await get_pool()
    snapshots: list[ProductSnapshot] = []

    async with pool.acquire() as conn:
        # Aggregate current state from product_listings
        rows = await conn.fetch("""
            SELECT
                pl.master_product_id,
                mp.canonical_name,
                mp.category,
                COUNT(DISTINCT pl.id)               AS listing_count,
                COUNT(DISTINCT pl.marketplace_id)    AS marketplace_count,
                COUNT(DISTINCT pl.seller_name)
                    FILTER (WHERE pl.seller_name IS NOT NULL) AS seller_count,
                COALESCE(AVG(pl.price), 0)          AS avg_price,
                COALESCE(MIN(pl.price), 0)          AS min_price,
                COALESCE(MAX(pl.price), 0)          AS max_price,
                COALESCE(SUM(pl.reviews_count), 0)  AS total_reviews,
                COALESCE(SUM(pl.sales_count), 0)    AS total_sales
            FROM product_listings pl
            JOIN master_products mp ON mp.id = pl.master_product_id
            GROUP BY pl.master_product_id, mp.canonical_name, mp.category
            HAVING COUNT(DISTINCT pl.id) >= 1
            ORDER BY listing_count DESC
        """)

        for row in rows:
            snap = ProductSnapshot(
                master_product_id=row["master_product_id"],
                canonical_name=row["canonical_name"],
                category=row["category"],
                listing_count=row["listing_count"],
                marketplace_count=row["marketplace_count"],
                seller_count=row["seller_count"],
                avg_price=float(row["avg_price"]),
                min_price=float(row["min_price"]),
                max_price=float(row["max_price"]),
                total_reviews=row["total_reviews"],
                total_sales=row["total_sales"],
            )
            snapshots.append(snap)

            # Upsert snapshot
            await conn.execute("""
                INSERT INTO product_snapshots
                    (master_product_id, snapshot_date, listing_count,
                     marketplace_count, seller_count, avg_price, min_price,
                     max_price, total_reviews, total_sales)
                VALUES ($1, CURRENT_DATE, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (master_product_id, snapshot_date)
                DO UPDATE SET
                    listing_count = EXCLUDED.listing_count,
                    marketplace_count = EXCLUDED.marketplace_count,
                    seller_count = EXCLUDED.seller_count,
                    avg_price = EXCLUDED.avg_price,
                    min_price = EXCLUDED.min_price,
                    max_price = EXCLUDED.max_price,
                    total_reviews = EXCLUDED.total_reviews,
                    total_sales = EXCLUDED.total_sales
            """,
                snap.master_product_id,
                snap.listing_count,
                snap.marketplace_count,
                snap.seller_count,
                snap.avg_price,
                snap.min_price,
                snap.max_price,
                snap.total_reviews,
                snap.total_sales,
            )

            # Update master_products aggregates
            await conn.execute("""
                UPDATE master_products SET
                    listing_count = $2,
                    marketplace_count = $3,
                    seller_count = $4,
                    avg_price = $5,
                    last_snapshot_at = NOW()
                WHERE id = $1
            """,
                snap.master_product_id,
                snap.listing_count,
                snap.marketplace_count,
                snap.seller_count,
                snap.avg_price,
            )

    logger.info("Captured %d product snapshots", len(snapshots))
    return snapshots


async def compute_velocities() -> list[TrendResult]:
    """
    Compute listing velocity and trend scores for all products.

    Velocity = (current_count - past_count) / past_count over a time window.
    Trend score 0-100 = weighted composite of:
      - 7-day velocity (40%)
      - marketplace breadth (20%)
      - listing volume (20%)
      - review/sales signals (20%)
    """
    pool = await get_pool()
    results: list[TrendResult] = []

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            WITH current_snap AS (
                SELECT master_product_id, listing_count, marketplace_count,
                       seller_count, avg_price, total_reviews, total_sales
                FROM product_snapshots
                WHERE snapshot_date = CURRENT_DATE
            ),
            snap_7d AS (
                SELECT DISTINCT ON (master_product_id)
                    master_product_id, listing_count
                FROM product_snapshots
                WHERE snapshot_date <= CURRENT_DATE - INTERVAL '7 days'
                ORDER BY master_product_id, snapshot_date DESC
            ),
            snap_30d AS (
                SELECT DISTINCT ON (master_product_id)
                    master_product_id, listing_count
                FROM product_snapshots
                WHERE snapshot_date <= CURRENT_DATE - INTERVAL '30 days'
                ORDER BY master_product_id, snapshot_date DESC
            )
            SELECT
                mp.id,
                mp.canonical_name,
                mp.category,
                mp.first_seen_at,
                cs.listing_count,
                cs.marketplace_count,
                cs.seller_count,
                cs.avg_price,
                cs.total_reviews,
                cs.total_sales,
                COALESCE(s7.listing_count, 0)  AS count_7d_ago,
                COALESCE(s30.listing_count, 0) AS count_30d_ago
            FROM master_products mp
            JOIN current_snap cs ON cs.master_product_id = mp.id
            LEFT JOIN snap_7d s7 ON s7.master_product_id = mp.id
            LEFT JOIN snap_30d s30 ON s30.master_product_id = mp.id
            ORDER BY cs.listing_count DESC
        """)

        for row in rows:
            current = row["listing_count"]
            count_7d = row["count_7d_ago"]
            count_30d = row["count_30d_ago"]
            first_seen = row["first_seen_at"]

            # Velocity: growth rate (handles zero-division)
            vel_7d = _velocity(current, count_7d)
            vel_30d = _velocity(current, count_30d)

            # Days since discovery
            days_since = 0
            is_new = True
            if first_seen:
                delta = datetime.now(timezone.utc) - first_seen
                days_since = max(0, delta.days)
                is_new = days_since <= NEW_PRODUCT_WINDOW_DAYS

            # Composite trend score 0-100
            trend_score = _compute_trend_score(
                velocity_7d=vel_7d,
                marketplace_count=row["marketplace_count"],
                listing_count=current,
                total_reviews=row["total_reviews"],
                total_sales=row["total_sales"],
                is_new=is_new,
            )

            is_trending = (
                vel_7d >= TRENDING_VELOCITY_THRESHOLD
                and current >= TRENDING_MIN_LISTINGS
            )

            # Determine trend label
            if is_new and current >= TRENDING_MIN_LISTINGS:
                trend_label = "emerging"
            elif vel_7d > 1.0:
                trend_label = "surging"
            elif vel_7d > TRENDING_VELOCITY_THRESHOLD:
                trend_label = "rising"
            elif vel_7d > 0:
                trend_label = "growing"
            elif vel_7d < -0.3:
                trend_label = "declining"
            else:
                trend_label = "stable"

            # Persist to master_products
            await conn.execute("""
                UPDATE master_products SET
                    trend_score = $2,
                    trend_label = $3,
                    velocity_7d = $4,
                    velocity_30d = $5,
                    is_trending = $6
                WHERE id = $1
            """,
                row["id"], trend_score, trend_label,
                vel_7d, vel_30d, is_trending,
            )

            results.append(TrendResult(
                master_product_id=row["id"],
                product_name=row["canonical_name"],
                category=row["category"],
                avg_price=float(row["avg_price"]),
                marketplace_count=row["marketplace_count"],
                listing_count=current,
                trend_score=trend_score,
                velocity_7d=vel_7d,
                velocity_30d=vel_30d,
                is_new=is_new,
                days_since_discovery=days_since,
            ))

    trending_count = sum(1 for r in results if r.trend_score >= 50)
    new_count = sum(1 for r in results if r.is_new)
    logger.info(
        "Velocity computed for %d products: %d trending, %d new",
        len(results), trending_count, new_count,
    )
    return results


def _velocity(current: int, past: int) -> float:
    """Growth rate: (current - past) / max(past, 1)."""
    if past <= 0:
        return float(current) if current > 0 else 0.0
    return (current - past) / past


def _compute_trend_score(
    velocity_7d: float,
    marketplace_count: int,
    listing_count: int,
    total_reviews: int,
    total_sales: int,
    is_new: bool,
) -> float:
    """
    Composite trend score 0-100.

    Weights:
      - velocity signal (40%): log-scaled 7d growth rate
      - marketplace breadth (20%): more marketplaces = stronger signal
      - listing volume (20%): log-scaled listing count
      - demand signals (20%): reviews + sales as social proof
    """
    # Velocity component: sigmoid-like mapping to 0-100
    vel_score = min(100, max(0, 50 + 30 * math.tanh(velocity_7d)))

    # Marketplace breadth: 1 mp = 25, 2 = 50, 3+ = 75-100
    breadth_score = min(100, marketplace_count * 33.3)

    # Volume: log scale, 1 listing = 0, 10 = 50, 100 = 100
    vol_score = min(100, math.log10(max(listing_count, 1)) * 50)

    # Demand: reviews + sales as proxy
    demand_raw = total_reviews + total_sales * 2
    demand_score = min(100, math.log10(max(demand_raw, 1)) * 30)

    # New product bonus: recently discovered products get a boost
    new_bonus = 10 if is_new else 0

    score = (
        vel_score * 0.40
        + breadth_score * 0.20
        + vol_score * 0.20
        + demand_score * 0.20
        + new_bonus
    )
    return round(min(100, max(0, score)), 1)


async def run_discovery_cycle() -> dict:
    """
    Full discovery cycle: snapshot → velocities → return summary.
    Designed to run after each scrape+process cycle or on a schedule.
    """
    snapshots = await take_snapshots()
    trends = await compute_velocities()

    trending = [t for t in trends if t.trend_score >= 50]
    new_products = [t for t in trends if t.is_new]

    summary = {
        "total_products": len(snapshots),
        "trending_count": len(trending),
        "new_products_count": len(new_products),
        "top_trending": [
            {
                "product_id": t.master_product_id,
                "product_name": t.product_name,
                "category": t.category,
                "avg_price": t.avg_price,
                "marketplace_count": t.marketplace_count,
                "listing_count": t.listing_count,
                "trend_score": t.trend_score,
                "velocity_7d": t.velocity_7d,
            }
            for t in sorted(trending, key=lambda x: x.trend_score, reverse=True)[:10]
        ],
        "newly_discovered": [
            {
                "product_id": t.master_product_id,
                "product_name": t.product_name,
                "category": t.category,
                "listing_count": t.listing_count,
                "days_since_discovery": t.days_since_discovery,
            }
            for t in sorted(new_products, key=lambda x: x.listing_count, reverse=True)[:10]
        ],
    }

    logger.info(
        "Discovery cycle: %d products, %d trending, %d new",
        summary["total_products"],
        summary["trending_count"],
        summary["new_products_count"],
    )
    return summary
