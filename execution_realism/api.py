"""
Execution Realism API — exposes realism check status and configuration.
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from .config import ExecutionRealismConfig
from .realism_guard import RealismGuard

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/realism", tags=["execution-realism"])

_guard: RealismGuard | None = None


def get_realism_guard() -> RealismGuard:
    global _guard
    if _guard is None:
        _guard = RealismGuard()
    return _guard


class RealismConfigResponse(BaseModel):
    max_price_deviation_pct: float
    min_profit_after_recheck_usd: float
    low_stock_threshold: int
    max_latency_seconds: float
    base_slippage_pct: float
    min_execution_confidence: float


@router.get("/config", response_model=RealismConfigResponse)
async def get_realism_config():
    """Current execution realism thresholds."""
    guard = get_realism_guard()
    cfg = guard._cfg
    return RealismConfigResponse(
        max_price_deviation_pct=cfg.price.max_price_deviation_pct,
        min_profit_after_recheck_usd=cfg.price.min_profit_after_recheck_usd,
        low_stock_threshold=cfg.stock.low_stock_threshold,
        max_latency_seconds=cfg.latency.max_latency_seconds,
        base_slippage_pct=cfg.slippage.base_slippage_pct,
        min_execution_confidence=cfg.min_execution_confidence,
    )
