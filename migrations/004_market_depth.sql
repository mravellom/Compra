-- ============================================================
-- Migration 004: Market Depth module
-- Estimates daily/monthly sales capacity for scalability analysis
-- ============================================================

ALTER TABLE opportunities
    ADD COLUMN IF NOT EXISTS market_depth_score REAL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS estimated_daily_sales REAL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS estimated_monthly_sales REAL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS scalability_level TEXT DEFAULT 'low';

CREATE INDEX IF NOT EXISTS idx_opportunities_depth
    ON opportunities (market_depth_score DESC)
    WHERE status = 'active';
