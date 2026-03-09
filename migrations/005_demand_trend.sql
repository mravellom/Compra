-- ============================================================
-- Migration 005: Demand Trend Detection
-- Detects rising/stable/declining demand from price + sales signals
-- ============================================================

ALTER TABLE opportunities
    ADD COLUMN IF NOT EXISTS demand_trend_score REAL DEFAULT 50,
    ADD COLUMN IF NOT EXISTS demand_trend_label TEXT DEFAULT 'stable';

CREATE INDEX IF NOT EXISTS idx_opportunities_demand_trend
    ON opportunities (demand_trend_score DESC)
    WHERE status = 'active';
