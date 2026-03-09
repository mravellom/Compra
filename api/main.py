import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes.alerts import router as alerts_router
from .routes.categories import router as categories_router
from .routes.opportunities import router as opportunities_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="Radar de Oportunidades API",
    description="API para deteccion de arbitraje entre marketplaces",
    version="0.1.0",
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


@app.get("/health")
async def health():
    return {"status": "ok"}
