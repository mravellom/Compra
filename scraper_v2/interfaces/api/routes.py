"""FastAPI routes for scraper_v2 monitoring and control."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/scraper/v2", tags=["scraper-v2"])


class ScraperStatusResponse(BaseModel):
    status: str
    cycle_count: int
    active_workers: int
    listings_total: int
    listings_per_second: float
    blocks_detected: int
    proxies_healthy: int
    proxies_total: int
    circuits_open: int


class HealthResponse(BaseModel):
    status: str
    proxies: dict
    circuits: dict
    rate_limits: dict
    bulkheads: dict


# These will be set by the factory during app startup
_orchestrator = None
_health_service = None


def set_services(orchestrator: object, health_service: object) -> None:
    global _orchestrator, _health_service
    _orchestrator = orchestrator
    _health_service = health_service


@router.get("/status")
async def get_status() -> dict:
    """Get current scraper status."""
    if not _orchestrator:
        raise HTTPException(503, "Scraper not initialized")
    return {
        "status": "running" if _orchestrator._running else "idle",
        "cycle_count": _orchestrator._cycle_count,
    }


@router.get("/health")
async def get_health() -> dict:
    """Get detailed health status."""
    if not _health_service:
        raise HTTPException(503, "Health service not initialized")
    health = await _health_service.check()
    return {
        "status": health.status,
        "proxies": health.proxies,
        "circuits": health.circuits,
        "rate_limits": health.rate_limits,
        "bulkheads": health.bulkheads,
    }


@router.post("/trigger")
async def trigger_cycle() -> dict:
    """Manually trigger a scraping cycle."""
    if not _orchestrator:
        raise HTTPException(503, "Scraper not initialized")
    if _orchestrator._running:
        raise HTTPException(409, "Cycle already in progress")

    import asyncio
    asyncio.create_task(_orchestrator.run_cycle())
    return {"message": "Cycle triggered"}
