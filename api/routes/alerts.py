from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import AlertConfig
from ..schemas import AlertConfigCreate, AlertConfigOut

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.post("/config", response_model=AlertConfigOut, status_code=201)
async def create_or_update_alert_config(
    payload: AlertConfigCreate,
    db: AsyncSession = Depends(get_db),
):
    """Crea o actualiza la configuracion de alertas de un usuario."""
    result = await db.execute(
        select(AlertConfig).where(AlertConfig.user_id == payload.user_id)
    )
    existing = result.scalar_one_or_none()

    if existing:
        for field, value in payload.model_dump().items():
            setattr(existing, field, value)
        existing.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(existing)
        return existing

    config = AlertConfig(**payload.model_dump())
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


@router.get("/config/{user_id}", response_model=AlertConfigOut)
async def get_alert_config(
    user_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Obtiene la configuracion de alertas de un usuario."""
    result = await db.execute(
        select(AlertConfig).where(AlertConfig.user_id == user_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Alert config not found")
    return config


@router.delete("/config/{user_id}", status_code=204)
async def delete_alert_config(
    user_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Elimina la configuracion de alertas de un usuario."""
    result = await db.execute(
        select(AlertConfig).where(AlertConfig.user_id == user_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Alert config not found")
    await db.delete(config)
    await db.commit()
