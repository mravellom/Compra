import asyncio
import logging
import os
import time

from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes.alerts import router as alerts_router
from .routes.categories import router as categories_router
from .routes.discovery import router as discovery_router
from .routes.opportunities import router as opportunities_router
from .routes.monetization import router as monetization_router, init_monetization, get_health_monitor
from .routes.scoring import router as scoring_router
from .routes.trades import router as trades_router
from .routes.truth import router as truth_router

# Engine routers
from engines.prediction.api.routes import router as prediction_router
from engines.trend.api.routes import router as trend_router
from engines.execution.api.routes import router as execution_router
from engines.orchestrator.api.routes import router as orchestrator_router
from engines.portfolio_optimizer.api.routes import router as optimizer_router
from capital_management.api import router as capital_router
from execution_realism.api import router as realism_router

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "300"))  # 5 minutes


async def _auto_scan_loop():
    """Background task: scan for opportunities every SCAN_INTERVAL seconds."""
    from .opportunity import detect_opportunities
    from .database import async_session

    await asyncio.sleep(30)  # wait for startup
    while True:
        try:
            async with async_session() as db:
                opps = await detect_opportunities(db)
                logger.info("Auto-scan: %d opportunities found", len(opps))
        except Exception:
            logger.error("Auto-scan error", exc_info=True)
        await asyncio.sleep(SCAN_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.getenv("API_KEY"):
        logger.warning("API_KEY not set — API is UNAUTHENTICATED (localhost only). Set API_KEY for production.")

    # Initialize monetization publisher
    publisher = init_monetization()
    logger.info("Monetization publisher initialized with %d channels", len(publisher.channels))

    # Start health monitor
    health_monitor = get_health_monitor()
    monitor_task = None
    if health_monitor:
        monitor_task = asyncio.create_task(health_monitor.start())
        logger.info("Health monitor started")

    # Start health alerter
    from .health_alerter import HealthAlerter
    health_alerter_instance = HealthAlerter()
    health_alerter_task = asyncio.create_task(health_alerter_instance.start())
    logger.info("Health alerter started")

    scan_task = asyncio.create_task(_auto_scan_loop())
    logger.info("Auto-scan started (every %ds)", SCAN_INTERVAL)
    yield
    scan_task.cancel()
    await health_alerter_instance.stop()
    health_alerter_task.cancel()
    if monitor_task:
        if health_monitor:
            await health_monitor.stop()
        monitor_task.cancel()


app = FastAPI(
    title="Radar de Oportunidades API",
    description="API para deteccion de arbitraje entre marketplaces",
    version="0.2.0",
    lifespan=lifespan,
)

ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS", "http://localhost:4200,http://localhost"
).split(",")

# Production middleware: request logging, rate limiting, API key auth
from .middleware import ProductionMiddleware
app.add_middleware(ProductionMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(opportunities_router, prefix="/api/v1")
app.include_router(alerts_router, prefix="/api/v1")
app.include_router(categories_router, prefix="/api/v1")
app.include_router(discovery_router, prefix="/api/v1")

# Engine routers
app.include_router(prediction_router, prefix="/api/v1")
app.include_router(trend_router, prefix="/api/v1")
app.include_router(execution_router, prefix="/api/v1")
app.include_router(orchestrator_router, prefix="/api/v1")
app.include_router(optimizer_router, prefix="/api/v1")

# Scoring profiles
app.include_router(scoring_router, prefix="/api/v1")

# Truth engine & observability
app.include_router(truth_router, prefix="/api/v1")

# Trade outcomes & accuracy
app.include_router(trades_router, prefix="/api/v1")

# Capital management
app.include_router(capital_router, prefix="/api/v1")

# Execution realism
app.include_router(realism_router, prefix="/api/v1")

# Monetization & observability
app.include_router(monetization_router)


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": time.time()}


@app.get("/health/pipeline")
async def pipeline_health():
    """Production health check: DB freshness, Redis stream, opportunity quality."""
    import redis.asyncio as aioredis
    from .database import async_session
    from sqlalchemy import text

    alerts = []
    status = "ok"

    # 1. Check DB connectivity + listing freshness
    db_status = "ok"
    freshness_minutes = None
    try:
        async with async_session() as db:
            row = await db.execute(text(
                "SELECT EXTRACT(EPOCH FROM (now() - max(scraped_at)))/60 "
                "AS minutes_since_last FROM product_listings"
            ))
            freshness_minutes = row.scalar()
            if freshness_minutes is not None:
                if freshness_minutes > 60:
                    db_status = "critical"
                    alerts.append(f"CRITICAL: No new listings for {freshness_minutes:.0f} min")
                elif freshness_minutes > 30:
                    db_status = "warning"
                    alerts.append(f"WARNING: No new listings for {freshness_minutes:.0f} min")

            # Opportunity quality
            row = await db.execute(text(
                "SELECT count(*) as total, "
                "count(*) FILTER (WHERE confidence_level = 'high') as high, "
                "round(avg(opportunity_score)::numeric, 1) as avg_score "
                "FROM opportunities WHERE status = 'active'"
            ))
            opp_stats = row.mappings().first()
    except Exception as e:
        db_status = "critical"
        alerts.append(f"CRITICAL: DB unreachable: {e}")
        opp_stats = None

    # 2. Check Redis stream lag
    redis_status = "ok"
    stream_info = {}
    try:
        r = aioredis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        try:
            stream_len = await r.xlen("raw_listings_queue")
            groups = await r.xinfo_groups("raw_listings_queue")
            lag = 0
            pending = 0
            for g in groups:
                lag = max(lag, g.get("lag", 0))
                pending = max(pending, g.get("pending", 0))

            stream_info = {
                "stream_length": stream_len,
                "consumer_lag": lag,
                "pending_messages": pending,
            }

            if lag > 50000:
                redis_status = "critical"
                alerts.append(f"CRITICAL: Redis consumer lag = {lag}")
            elif lag > 10000:
                redis_status = "warning"
                alerts.append(f"WARNING: Redis consumer lag = {lag}")
        finally:
            await r.aclose()
    except Exception as e:
        redis_status = "critical"
        alerts.append(f"CRITICAL: Redis unreachable: {e}")

    # 3. Overall status
    statuses = [db_status, redis_status]
    if "critical" in statuses:
        status = "critical"
    elif "warning" in statuses:
        status = "warning"

    return {
        "status": status,
        "timestamp": time.time(),
        "database": {
            "status": db_status,
            "minutes_since_last_listing": round(freshness_minutes, 1) if freshness_minutes else None,
        },
        "redis": {
            "status": redis_status,
            **stream_info,
        },
        "opportunities": {
            "total": opp_stats["total"] if opp_stats else 0,
            "high_confidence": opp_stats["high"] if opp_stats else 0,
            "avg_score": float(opp_stats["avg_score"]) if opp_stats and opp_stats["avg_score"] else 0,
        },
        "alerts": alerts,
    }


