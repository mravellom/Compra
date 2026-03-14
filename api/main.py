import asyncio
import logging
import os

from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes.alerts import router as alerts_router
from .routes.categories import router as categories_router
from .routes.discovery import router as discovery_router
from .routes.opportunities import router as opportunities_router

logging.basicConfig(
    level=logging.INFO,
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
    task = asyncio.create_task(_auto_scan_loop())
    logger.info("Auto-scan started (every %ds)", SCAN_INTERVAL)
    yield
    task.cancel()


app = FastAPI(
    title="Radar de Oportunidades API",
    description="API para deteccion de arbitraje entre marketplaces",
    version="0.1.0",
    lifespan=lifespan,
)

ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS", "http://localhost:4200,http://localhost"
).split(",")

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


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/pipeline")
async def pipeline_health():
    """Detailed pipeline health check with stream metrics."""
    try:
        from infra.monitor import PipelineMonitor
        monitor = PipelineMonitor()
        snapshot = await monitor.collect_snapshot()
        return {
            "status": snapshot.overall_status,
            "dlq_length": snapshot.dlq_length,
            "alerts": snapshot.alerts,
            "streams": {
                name: {
                    "length": h.length,
                    "consumer_lag": h.consumer_lag,
                    "consumers": h.consumers,
                    "status": h.status,
                }
                for name, h in snapshot.streams.items()
            },
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}
