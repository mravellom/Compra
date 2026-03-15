-- Migration 018: Orchestrator pipeline tracking table
-- Run: psql -U postgres -d compraventa -f migrations/018_orchestrator_pipelines.sql

BEGIN;

CREATE TABLE IF NOT EXISTS orchestrator_pipelines (
    id              BIGSERIAL,
    pipeline_id     TEXT PRIMARY KEY,
    opportunity_id  BIGINT NOT NULL,
    product_id      BIGINT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued',           -- queued, running, completed, partial, failed
    decision        TEXT,                                     -- execute, monitor, skip, hold
    decision_score  REAL,
    signal_strength TEXT,                                     -- strong, moderate, weak, conflicting
    phases          JSONB DEFAULT '{}',                       -- per-phase status, duration, data
    reasons         JSONB DEFAULT '[]',                       -- policy evaluation reasons
    total_duration_ms REAL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_orch_opportunity ON orchestrator_pipelines(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_orch_product ON orchestrator_pipelines(product_id);
CREATE INDEX IF NOT EXISTS idx_orch_decision ON orchestrator_pipelines(decision);
CREATE INDEX IF NOT EXISTS idx_orch_created ON orchestrator_pipelines(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orch_status ON orchestrator_pipelines(status);

COMMIT;
