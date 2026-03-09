-- Migration 008: Capital Efficiency Scoring
-- Prioritize opportunities that generate high profit with low capital investment

ALTER TABLE opportunities
    ADD COLUMN IF NOT EXISTS capital_required NUMERIC(12,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS capital_efficiency_score REAL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS capital_tier TEXT DEFAULT 'medium',
    ADD COLUMN IF NOT EXISTS recommended_quantity INTEGER DEFAULT 1;
