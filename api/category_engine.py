"""
Category Arbitrage Analytics Engine.

1. Classifies products into categories via keyword-based NLP on titles
2. Aggregates opportunity metrics per category
3. Scores categories for consistent arbitrage potential
"""
import logging

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CategoryArbitrageStats, MasterProduct, Opportunity

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
# NLP Category Classifier (inline, same rules as processor/categorizer.py)
# ──────────────────────────────────────────────────────────
import re

CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("Laptops", [r"\blaptop\b", r"\bnotebook\b", r"\bmacbook\b", r"\bchromebook\b", r"\bthinkpad\b"]),
    ("Tablets", [r"\bipad\b", r"\btablet\b", r"\bgalaxy\s+tab\b", r"\bfire\s+hd\b"]),
    ("Smartphones", [r"\biphone\b", r"\bsmartphone\b", r"\bcelular\b", r"\bgalaxy\s+s\d", r"\bgalaxy\s+z\b", r"\bpixel\s+\d", r"\bmoto\s+g\b", r"\bredmi\b"]),
    ("Smartwatches", [r"\bapple\s+watch\b", r"\bsmartwatch\b", r"\bgalaxy\s+watch\b", r"\bfitbit\b", r"\bamazfit\b"]),
    ("Headphones", [r"\bheadphone\b", r"\bearphone\b", r"\bearbuds?\b", r"\bairpods?\b", r"\bwh.?1000", r"\bwf.?1000", r"\baudifonos?\b"]),
    ("Speakers", [r"\bspeaker\b", r"\bbocina\b", r"\bsoundbar\b", r"\bsubwoofer\b", r"\bhomepod\b", r"\bsonos\b"]),
    ("Gaming Consoles", [r"\bplaystation\b", r"\bps[45]\b", r"\bxbox\b", r"\bnintendo\s+switch\b", r"\bsteam\s+deck\b", r"\bconsola\b"]),
    ("Gaming Accessories", [r"\bcontroller\b", r"\bgaming\s+(?:mouse|keyboard|headset)\b", r"\bjoystick\b"]),
    ("Video Games", [r"\bjuego\b.*\b(?:ps[45]|xbox|switch)\b", r"\bgame\b.*\b(?:ps[45]|xbox|switch|pc)\b"]),
    ("Cameras", [r"\bcamera\b", r"\bcamara\b", r"\bmirrorless\b", r"\bdslr\b", r"\bgopro\b", r"\binstax\b"]),
    ("Drones", [r"\bdrone\b", r"\bdji\b.*\b(?:mini|air|mavic|avata)\b"]),
    ("TVs & Monitors", [r"\btv\b", r"\btelevisi[oó]n\b", r"\bmonitor\b", r"\boled\b", r"\bqled\b", r"\bsmart\s+tv\b", r"\bpantalla\b"]),
    ("Smart Home", [r"\balexa\b", r"\becho\b", r"\bgoogle\s+(?:home|nest)\b", r"\bthermostat\b"]),
    ("GPUs", [r"\bgpu\b", r"\bgraphics\s+card\b", r"\brtx\s+\d", r"\bgtx\s+\d", r"\bradeon\s+rx\b", r"\btarjeta\s+(?:de\s+)?video\b"]),
    ("CPUs & Components", [r"\bcpu\b", r"\bprocessor\b", r"\bprocesador\b", r"\bryzen\b", r"\bcore\s+i[3579]\b", r"\bssd\b", r"\bnvme\b"]),
    ("Phone Accessories", [r"\bfunda\b", r"\bscreen\s+protector\b", r"\bcharger\b", r"\bcargador\b", r"\bpower\s+bank\b"]),
    ("Networking", [r"\brouter\b", r"\bwifi\b.*\b(?:mesh|extender|6e?)\b"]),
    ("Storage", [r"\bmicro\s*sd\b", r"\bsd\s+card\b", r"\busb\s+(?:drive|flash)\b", r"\bexternal\s+(?:hard|ssd|hdd)\b", r"\bdisco\s+duro\b"]),
]

_COMPILED_RULES = [
    (cat, [re.compile(p, re.IGNORECASE) for p in patterns])
    for cat, patterns in CATEGORY_RULES
]


def classify_product(title: str) -> str:
    """Classify a product title into a category. Returns 'Other' if no match."""
    for category, patterns in _COMPILED_RULES:
        for pattern in patterns:
            if pattern.search(title):
                return category
    return "Other"


# ──────────────────────────────────────────────────────────
# Backfill: assign categories to master_products where NULL
# ──────────────────────────────────────────────────────────
async def backfill_categories(db: AsyncSession) -> int:
    """
    Classify uncategorized master_products by scanning their listing titles.
    Returns the number of products updated.
    """
    result = await db.execute(
        select(MasterProduct.id, MasterProduct.canonical_name)
        .where(MasterProduct.category.is_(None))
    )
    rows = result.all()
    if not rows:
        return 0

    updated = 0
    for mp_id, name in rows:
        category = classify_product(name)
        await db.execute(
            text("UPDATE master_products SET category = :cat WHERE id = :id"),
            {"cat": category, "id": mp_id},
        )
        updated += 1

    await db.flush()
    logger.info("Backfilled categories for %d products", updated)
    return updated


# ──────────────────────────────────────────────────────────
# Category scoring
# ──────────────────────────────────────────────────────────

# Thresholds for a "hot" arbitrage category
CAT_MIN_AVG_ROI = 0.40       # 40% average ROI
CAT_MIN_VELOCITY = 20.0      # minimum avg velocity score
CAT_MAX_COMPETITION = 70.0   # not too saturated
CAT_MIN_OPPORTUNITIES = 2    # need at least 2 data points

# Category score weights
CW_ROI = 0.30
CW_PROFIT = 0.20
CW_VELOCITY = 0.20
CW_COMPETITION = 0.15
CW_DEMAND = 0.15


def compute_category_score(
    avg_roi: float,
    avg_profit: float,
    avg_velocity: float,
    avg_competition: float,
    avg_demand_trend: float,
    opportunity_count: int,
) -> float:
    """
    Composite category score 0-100.
    Higher = more profitable and scalable category for arbitrage.
    """
    # Normalize ROI: 100% avg ROI = 100 score
    roi_norm = min(100, avg_roi * 100) if avg_roi > 0 else 0

    # Normalize profit: $200 avg = 100
    profit_norm = min(100, (avg_profit / 200) * 100) if avg_profit > 0 else 0

    # Velocity already 0-100
    vel_norm = avg_velocity

    # Competition inverse: low = good
    comp_norm = max(0, 100 - avg_competition)

    # Demand trend already 0-100
    demand_norm = avg_demand_trend

    raw_score = (
        roi_norm * CW_ROI
        + profit_norm * CW_PROFIT
        + vel_norm * CW_VELOCITY
        + comp_norm * CW_COMPETITION
        + demand_norm * CW_DEMAND
    )

    # Volume bonus: more opportunities = more confidence in the signal
    if opportunity_count >= 10:
        volume_boost = 1.1
    elif opportunity_count >= 5:
        volume_boost = 1.05
    else:
        volume_boost = 1.0

    return round(min(100, max(0, raw_score * volume_boost)), 1)


# ──────────────────────────────────────────────────────────
# Main: refresh category stats from live opportunity data
# ──────────────────────────────────────────────────────────
async def refresh_category_stats(db: AsyncSession) -> list[CategoryArbitrageStats]:
    """
    Aggregate active opportunities by category, compute scores,
    and upsert into category_arbitrage_stats.
    """
    # Step 1: backfill any uncategorized products
    await backfill_categories(db)

    # Step 2: aggregate active opportunities grouped by category
    agg_query = (
        select(
            MasterProduct.category,
            func.count(func.distinct(Opportunity.master_product_id)).label("total_products"),
            func.count(Opportunity.id).label("opportunity_count"),
            func.avg(Opportunity.net_profit).label("avg_profit"),
            func.avg(Opportunity.roi).label("avg_roi"),
            func.avg(Opportunity.sales_velocity_score).label("avg_velocity"),
            func.avg(Opportunity.competition_score).label("avg_competition"),
            func.avg(Opportunity.demand_trend_score).label("avg_demand"),
            func.max(Opportunity.roi).label("best_roi"),
            func.max(Opportunity.net_profit).label("best_profit"),
        )
        .join(MasterProduct, Opportunity.master_product_id == MasterProduct.id)
        .where(Opportunity.status == "active")
        .where(MasterProduct.category.isnot(None))
        .group_by(MasterProduct.category)
    )

    result = await db.execute(agg_query)
    rows = result.all()

    if not rows:
        logger.info("No active opportunities with categories found")
        return []

    # Step 3: clear old stats and insert fresh
    await db.execute(delete(CategoryArbitrageStats))

    stats_list: list[CategoryArbitrageStats] = []

    for row in rows:
        cat_name = row.category or "Other"
        avg_profit = float(row.avg_profit or 0)
        avg_roi = float(row.avg_roi or 0)
        avg_vel = float(row.avg_velocity or 0)
        avg_comp = float(row.avg_competition or 0)
        avg_demand = float(row.avg_demand or 50)
        opp_count = int(row.opportunity_count or 0)

        score = compute_category_score(
            avg_roi, avg_profit, avg_vel, avg_comp, avg_demand, opp_count,
        )

        stat = CategoryArbitrageStats(
            category_name=cat_name,
            total_products=int(row.total_products or 0),
            opportunity_count=opp_count,
            avg_profit=round(avg_profit, 2),
            avg_roi=round(avg_roi, 4),
            avg_sales_velocity=round(avg_vel, 1),
            avg_competition=round(avg_comp, 1),
            avg_demand_trend=round(avg_demand, 1),
            best_roi=round(float(row.best_roi or 0), 4),
            best_profit=round(float(row.best_profit or 0), 2),
            category_score=score,
        )
        db.add(stat)
        stats_list.append(stat)

    await db.commit()

    stats_list.sort(key=lambda s: s.category_score, reverse=True)
    logger.info(
        "Refreshed %d category stats. Top: %s (score=%.1f)",
        len(stats_list),
        stats_list[0].category_name if stats_list else "N/A",
        stats_list[0].category_score if stats_list else 0,
    )
    return stats_list
