"""Repository for orchestrator pipeline persistence.

Stores pipeline execution history for auditability and replay.
Uses a dedicated table for pipeline runs.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..domain.enums import DecisionType
from ..domain.models import (
    IntelligencePipeline,
    OrchestratorDecision,
    OpportunityContext,
)

logger = logging.getLogger(__name__)


class OrchestratorRepository:
    """Handles persistence for orchestrator pipeline results."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save_pipeline(self, pipeline: IntelligencePipeline) -> None:
        """Upsert pipeline execution record."""
        async with self._session_factory() as session:
            await session.execute(
                text("""
                    INSERT INTO orchestrator_pipelines
                        (pipeline_id, opportunity_id, product_id, status,
                         decision, decision_score, signal_strength,
                         phases, reasons, total_duration_ms,
                         created_at, completed_at)
                    VALUES
                        (:pipeline_id, :opportunity_id, :product_id, :status,
                         :decision, :decision_score, :signal_strength,
                         :phases, :reasons, :total_duration_ms,
                         :created_at, :completed_at)
                    ON CONFLICT (pipeline_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        decision = EXCLUDED.decision,
                        decision_score = EXCLUDED.decision_score,
                        signal_strength = EXCLUDED.signal_strength,
                        phases = EXCLUDED.phases,
                        reasons = EXCLUDED.reasons,
                        total_duration_ms = EXCLUDED.total_duration_ms,
                        completed_at = EXCLUDED.completed_at
                """),
                {
                    "pipeline_id": pipeline.pipeline_id,
                    "opportunity_id": pipeline.opportunity.opportunity_id,
                    "product_id": pipeline.opportunity.product_id,
                    "status": pipeline.status.value,
                    "decision": pipeline.decision.decision.value if pipeline.decision else None,
                    "decision_score": pipeline.decision.score if pipeline.decision else None,
                    "signal_strength": pipeline.decision.signal_strength.value if pipeline.decision else None,
                    "phases": json.dumps(
                        {k: v.model_dump(mode="json") for k, v in pipeline.phases.items()},
                        default=str,
                    ),
                    "reasons": json.dumps(
                        pipeline.decision.reasons if pipeline.decision else [],
                    ),
                    "total_duration_ms": pipeline.total_duration_ms,
                    "created_at": pipeline.created_at,
                    "completed_at": pipeline.completed_at,
                },
            )
            await session.commit()

    async def get_pipeline(self, pipeline_id: str) -> Optional[dict]:
        """Retrieve a pipeline execution record (raw dict for API response)."""
        async with self._session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT pipeline_id, opportunity_id, product_id, status,
                           decision, decision_score, signal_strength,
                           phases, reasons, total_duration_ms,
                           created_at, completed_at
                    FROM orchestrator_pipelines
                    WHERE pipeline_id = :pid
                """),
                {"pid": pipeline_id},
            )
            row = result.fetchone()
            if not row:
                return None

            return {
                "pipeline_id": row[0],
                "opportunity_id": row[1],
                "product_id": row[2],
                "status": row[3],
                "decision": row[4],
                "decision_score": float(row[5]) if row[5] else None,
                "signal_strength": row[6],
                "phases": json.loads(row[7]) if row[7] else {},
                "reasons": json.loads(row[8]) if row[8] else [],
                "total_duration_ms": float(row[9]) if row[9] else None,
                "created_at": row[10].isoformat() if row[10] else None,
                "completed_at": row[11].isoformat() if row[11] else None,
            }

    async def get_recent_decisions(self, limit: int = 20) -> list[dict]:
        """Retrieve recent pipeline decisions for the dashboard."""
        async with self._session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT pipeline_id, opportunity_id, product_id, status,
                           decision, decision_score, signal_strength,
                           total_duration_ms, created_at
                    FROM orchestrator_pipelines
                    ORDER BY created_at DESC
                    LIMIT :limit
                """),
                {"limit": limit},
            )
            rows = result.fetchall()
            return [
                {
                    "pipeline_id": r[0],
                    "opportunity_id": r[1],
                    "product_id": r[2],
                    "status": r[3],
                    "decision": r[4],
                    "decision_score": float(r[5]) if r[5] else None,
                    "signal_strength": r[6],
                    "total_duration_ms": float(r[7]) if r[7] else None,
                    "created_at": r[8].isoformat() if r[8] else None,
                }
                for r in rows
            ]

    async def get_decision_stats(self) -> dict:
        """Aggregate decision statistics."""
        async with self._session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT
                        decision,
                        COUNT(*) as count,
                        ROUND(AVG(decision_score)::numeric, 1) as avg_score,
                        ROUND(AVG(total_duration_ms)::numeric, 0) as avg_duration_ms
                    FROM orchestrator_pipelines
                    WHERE created_at >= now() - interval '24 hours'
                    GROUP BY decision
                    ORDER BY count DESC
                """)
            )
            rows = result.fetchall()
            return {
                "decisions_24h": [
                    {
                        "decision": r[0],
                        "count": r[1],
                        "avg_score": float(r[2]) if r[2] else 0,
                        "avg_duration_ms": float(r[3]) if r[3] else 0,
                    }
                    for r in rows
                ],
            }
