"""
Opportunity Lifecycle Tracking Engine.

Detects how arbitrage opportunities evolve over time:
- Records profit/ROI/competition snapshots each scan
- Calculates decay rate from historical trend
- Estimates remaining lifetime
- Classifies: fresh / stable / decaying
- Produces urgency_score (0-100) for prioritization
"""
import logging
import math
import statistics
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Opportunity, OpportunityHistory

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# Snapshot: record current state for future trend analysis
# ──────────────────────────────────────────────────────────
async def record_opportunity_snapshot(
    db: AsyncSession,
    opp: Opportunity,
) -> None:
    """Save a point-in-time snapshot of an opportunity's key metrics."""
    snapshot = OpportunityHistory(
        opportunity_id=opp.id,
        master_product_id=opp.master_product_id,
        net_profit=opp.net_profit,
        roi=opp.roi,
        competitor_count=opp.competitor_count,
        buy_price=opp.buy_price,
        sell_price=opp.sell_price,
        opportunity_score=opp.opportunity_score,
    )
    db.add(snapshot)


# ──────────────────────────────────────────────────────────
# Decay detection: analyze historical snapshots
# ──────────────────────────────────────────────────────────
async def compute_lifecycle_metrics(
    db: AsyncSession,
    opp: Opportunity,
) -> tuple[float, float, float, str]:
    """
    Analyze opportunity history to compute:
    - decay_rate:     % profit loss per day (-1 to +1, negative = decaying)
    - lifetime_hours: how long this product has had an arbitrage window
    - urgency_score:  0-100, high = act now before it disappears
    - lifecycle_label: fresh / stable / decaying

    Returns (decay_rate, lifetime_hours, urgency_score, lifecycle_label).
    """
    # Fetch historical snapshots for this product (across opportunity cycles)
    result = await db.execute(
        select(OpportunityHistory)
        .where(OpportunityHistory.master_product_id == opp.master_product_id)
        .order_by(OpportunityHistory.recorded_at.asc())
    )
    snapshots = list(result.scalars().all())

    # --- Lifetime: time since first snapshot ---
    if snapshots:
        first_seen = snapshots[0].recorded_at
        now = datetime.now(timezone.utc)
        lifetime_hours = round((now - first_seen).total_seconds() / 3600, 1)
    else:
        lifetime_hours = 0.0

    # Not enough history for trend analysis
    if len(snapshots) < 2:
        return 0.0, lifetime_hours, 50.0, "fresh"

    # --- Signal 1: Profit trend (slope of net_profit over time) ---
    profits = [float(s.net_profit) for s in snapshots]
    profit_decay = _compute_slope_signal(profits)

    # --- Signal 2: ROI trend ---
    rois = [float(s.roi) for s in snapshots]
    roi_decay = _compute_slope_signal(rois)

    # --- Signal 3: Competition trend (increasing = bad) ---
    competitors = [s.competitor_count for s in snapshots]
    comp_trend = _compute_slope_signal(competitors)
    # Invert: rising competitors = negative signal for opportunity
    comp_decay = -comp_trend if comp_trend is not None else 0.0

    # --- Signal 4: Price convergence (buy-sell spread shrinking) ---
    spreads = [float(s.sell_price) - float(s.buy_price) for s in snapshots]
    spread_decay = _compute_slope_signal(spreads)

    # --- Composite decay rate ---
    # Weight: profit trend most important, then ROI, then spread, then competition
    signals = []
    weights = []

    if profit_decay is not None:
        signals.append(profit_decay)
        weights.append(0.35)
    if roi_decay is not None:
        signals.append(roi_decay)
        weights.append(0.25)
    if spread_decay is not None:
        signals.append(spread_decay)
        weights.append(0.25)
    if comp_decay != 0.0:
        signals.append(comp_decay)
        weights.append(0.15)

    if signals:
        total_w = sum(weights)
        decay_rate = sum(s * w for s, w in zip(signals, weights)) / total_w
    else:
        decay_rate = 0.0

    decay_rate = round(max(-1.0, min(1.0, decay_rate)), 4)

    # --- Urgency score ---
    urgency = _compute_urgency(decay_rate, lifetime_hours, len(snapshots))

    # --- Lifecycle label ---
    if lifetime_hours < 12 or len(snapshots) <= 2:
        label = "fresh"
    elif decay_rate <= -0.15:
        label = "decaying"
    else:
        label = "stable"

    return decay_rate, lifetime_hours, urgency, label


def _compute_slope_signal(values: list[float]) -> float | None:
    """
    Linear regression slope over a series, normalized to [-1, +1].
    Positive = increasing, negative = decreasing.
    """
    n = len(values)
    if n < 2:
        return None

    avg = statistics.mean(values)
    if avg == 0:
        return 0.0

    x_mean = (n - 1) / 2.0
    numerator = sum((i - x_mean) * (v - avg) for i, v in enumerate(values))
    denominator = sum((i - x_mean) ** 2 for i in range(n))

    if denominator == 0:
        return 0.0

    slope = numerator / denominator
    # Normalize: relative change per step as fraction of mean
    relative = slope / abs(avg)
    # Scale so that ~10% total relative change across window = +-1.0
    scaled = relative * n / 0.10
    return max(-1.0, min(1.0, scaled))


def _compute_urgency(
    decay_rate: float,
    lifetime_hours: float,
    snapshot_count: int,
) -> float:
    """
    Urgency score 0-100.

    High urgency when:
    - decay_rate is strongly negative (opportunity disappearing fast)
    - opportunity has been alive a long time (more likely to close soon)
    - enough data points to be confident in the trend

    Low urgency when:
    - decay_rate is positive or zero (stable/improving)
    - opportunity is fresh (just discovered)
    """
    # Base: map decay from [-1, +1] to [100, 0]
    # Negative decay (losing profit) = high urgency
    decay_component = max(0, min(100, (-decay_rate + 1) * 50))

    # Age component: older opportunities are inherently more urgent
    # 0h=0, 24h=30, 72h=60, 168h(1wk)=80, 336h(2wk)=95
    age_component = min(95, 30 * math.log2(lifetime_hours / 24 + 1)) if lifetime_hours > 0 else 0

    # Confidence: more snapshots = more reliable signal
    confidence = min(1.0, snapshot_count / 5.0)

    # Weighted blend: decay signal dominates when confident, age fills in for fresh opps
    if confidence > 0.5:
        urgency = decay_component * 0.65 + age_component * 0.35
    else:
        urgency = decay_component * 0.3 + age_component * 0.4 + 50 * (1 - confidence) * 0.3

    return round(max(0, min(100, urgency)), 1)


# ──────────────────────────────────────────────────────────
# Batch update: run after each scan cycle
# ──────────────────────────────────────────────────────────
async def update_all_lifecycles(db: AsyncSession) -> int:
    """
    For all active opportunities:
    1. Record a snapshot
    2. Compute lifecycle metrics
    3. Update the opportunity row

    Returns count of opportunities updated.
    """
    result = await db.execute(
        select(Opportunity).where(Opportunity.status == "active")
    )
    active_opps = list(result.scalars().all())

    if not active_opps:
        return 0

    for opp in active_opps:
        # Record snapshot
        await record_opportunity_snapshot(db, opp)

    # Flush so snapshots are visible to the lifecycle queries
    await db.flush()

    for opp in active_opps:
        decay_rate, lifetime_hours, urgency, label = await compute_lifecycle_metrics(db, opp)
        opp.decay_rate = decay_rate
        opp.lifetime_hours = lifetime_hours
        opp.urgency_score = urgency
        opp.lifecycle_label = label

    await db.flush()
    logger.info(
        "Lifecycle updated for %d opportunities (fresh: %d, stable: %d, decaying: %d)",
        len(active_opps),
        sum(1 for o in active_opps if o.lifecycle_label == "fresh"),
        sum(1 for o in active_opps if o.lifecycle_label == "stable"),
        sum(1 for o in active_opps if o.lifecycle_label == "decaying"),
    )
    return len(active_opps)
