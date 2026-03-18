"""
Truth Engine API routes — observability and metrics endpoints.

Exposes:
- /metrics — system precision, profit accuracy, time-to-sell
- /outcomes — trade outcome history
- /adaptive — current adaptive filter state
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/truth", tags=["truth-engine"])


# ── Response schemas ─────────────────────────────────────────

class MetricsResponse(BaseModel):
    precision: float = 0.0
    total_detected: int = 0
    total_executed: int = 0
    total_successful: int = 0
    success_rate: float = 0.0
    avg_profit_error_pct: float = 0.0
    avg_roi_error_pct: float = 0.0
    avg_actual_profit: float = 0.0
    avg_expected_profit: float = 0.0
    avg_time_to_sell_minutes: float = 0.0
    window_days: int = 30
    current_thresholds: dict = Field(default_factory=dict)


class TradeOutcomeResponse(BaseModel):
    id: int
    opportunity_id: int
    detected_at: datetime
    executed_at: datetime | None = None
    completed_at: datetime | None = None
    buy_price_predicted: float = 0
    sell_price_predicted: float = 0
    estimated_profit: float = 0
    expected_roi: float = 0
    buy_price_actual: float = 0
    sell_price_actual: float = 0
    actual_profit: float = 0
    actual_roi: float = 0
    time_to_sell_minutes: float | None = None
    sold: bool = False
    cancelled: bool = False
    failure_reason: str | None = None
    buy_marketplace: str = ""
    sell_marketplace: str = ""

    model_config = {"from_attributes": True}


class AdaptiveStateResponse(BaseModel):
    min_profit_usd: float
    min_roi: float
    min_confidence_score: float
    min_reviews_count: int
    min_seller_rating: float
    last_precision: float
    tighten_count: int
    relax_count: int


# ── Singleton instances (created lazily) ─────────────────────

_precision_calculator = None
_adaptive_engine = None
_resolver_feedback = None


def _get_precision_calculator(db: AsyncSession):
    """Lazy-init precision calculator."""
    global _precision_calculator
    from truth_engine.repository import TruthRepository
    from truth_engine.evaluator import TruthEvaluator
    from metrics.precision import PrecisionCalculator
    repo = TruthRepository(db)
    # Each request gets a fresh repo with its own session,
    # but the evaluator is stateless and reusable.
    return PrecisionCalculator(repo, TruthEvaluator())


def _get_adaptive_engine():
    """Get or create the adaptive filter engine singleton."""
    global _adaptive_engine
    if _adaptive_engine is None:
        from truth_engine.adaptive_filters import AdaptiveFilterEngine
        _adaptive_engine = AdaptiveFilterEngine()
    return _adaptive_engine


def _get_resolver_feedback():
    """Get or create the resolver feedback store singleton."""
    global _resolver_feedback
    if _resolver_feedback is None:
        from truth_engine.resolver_feedback import ResolverFeedbackStore
        _resolver_feedback = ResolverFeedbackStore()
    return _resolver_feedback


# ── Endpoints ────────────────────────────────────────────────

@router.get("/metrics", response_model=MetricsResponse)
async def get_metrics(
    window_days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """System precision and performance metrics."""
    calc = _get_precision_calculator(db)
    adaptive = _get_adaptive_engine()

    metrics = await calc.get_system_metrics(
        window_days=window_days,
        adaptive_thresholds=adaptive.get_current_thresholds(),
    )

    return MetricsResponse(
        precision=metrics.precision,
        total_detected=metrics.total_detected,
        total_executed=metrics.total_executed,
        total_successful=metrics.total_successful,
        success_rate=metrics.success_rate,
        avg_profit_error_pct=metrics.avg_profit_error_pct,
        avg_roi_error_pct=metrics.avg_roi_error_pct,
        avg_actual_profit=metrics.avg_actual_profit,
        avg_expected_profit=metrics.avg_expected_profit,
        avg_time_to_sell_minutes=metrics.avg_time_to_sell_minutes,
        window_days=metrics.window_days,
        current_thresholds=metrics.current_thresholds,
    )


@router.get("/outcomes", response_model=list[TradeOutcomeResponse])
async def list_outcomes(
    days: int = Query(30, ge=1, le=365),
    product_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List trade outcomes for review."""
    from truth_engine.repository import TruthRepository
    repo = TruthRepository(db)

    if product_id is not None:
        outcomes = await repo.get_outcomes_by_product(product_id, limit)
    else:
        start = datetime.now(timezone.utc) - timedelta(days=days)
        end = datetime.now(timezone.utc)
        outcomes = await repo.get_outcomes_by_date(start, end)

    return [
        TradeOutcomeResponse(
            id=o.id,
            opportunity_id=o.opportunity_id,
            detected_at=o.detected_at,
            executed_at=o.executed_at,
            completed_at=o.completed_at,
            buy_price_predicted=o.buy_price_predicted,
            sell_price_predicted=o.sell_price_predicted,
            estimated_profit=o.estimated_profit,
            expected_roi=o.expected_roi,
            buy_price_actual=o.buy_price_actual,
            sell_price_actual=o.sell_price_actual,
            actual_profit=o.actual_profit,
            actual_roi=o.actual_roi,
            time_to_sell_minutes=o.time_to_sell_minutes,
            sold=o.sold,
            cancelled=o.cancelled,
            failure_reason=o.failure_reason,
            buy_marketplace=o.buy_marketplace,
            sell_marketplace=o.sell_marketplace,
        )
        for o in outcomes[:limit]
    ]


@router.get("/adaptive", response_model=AdaptiveStateResponse)
async def get_adaptive_state():
    """Current adaptive filter thresholds."""
    engine = _get_adaptive_engine()
    t = engine.get_current_thresholds()
    return AdaptiveStateResponse(**t)


@router.post("/adaptive/adapt")
async def trigger_adaptation(
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger adaptive filter adjustment based on latest metrics."""
    calc = _get_precision_calculator(db)
    adaptive = _get_adaptive_engine()

    precision_metrics = await calc.compute()
    new_state = adaptive.adapt(precision_metrics)

    return {
        "adapted": True,
        "precision": precision_metrics.precision,
        "new_thresholds": adaptive.get_current_thresholds(),
    }


@router.get("/resolver-penalties")
async def get_resolver_penalties():
    """Current resolver feedback penalties."""
    store = _get_resolver_feedback()
    penalties = store.get_all_penalties()
    return [
        {
            "brand": p.brand,
            "model": p.model,
            "marketplace": p.marketplace,
            "penalty": p.penalty,
            "failure_count": p.failure_count,
            "last_failure_at": p.last_failure_at.isoformat() if p.last_failure_at else None,
        }
        for p in penalties
    ]
