-- ============================================================
-- Migration 007: Opportunity Lifecycle Tracking
-- Tracks how opportunities evolve and decay over time
-- ============================================================

-- 1. Historical snapshots: one row per opportunity per scan cycle
CREATE TABLE IF NOT EXISTS opportunity_history (
    id                  BIGSERIAL PRIMARY KEY,
    opportunity_id      BIGINT NOT NULL,
    master_product_id   BIGINT NOT NULL,
    net_profit          NUMERIC(12, 2) NOT NULL,
    roi                 REAL NOT NULL,
    competitor_count    INTEGER NOT NULL DEFAULT 0,
    buy_price           NUMERIC(12, 2) NOT NULL,
    sell_price          NUMERIC(12, 2) NOT NULL,
    opportunity_score   REAL NOT NULL DEFAULT 0,
    recorded_at         TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT fk_opp_history_product
        FOREIGN KEY (master_product_id) REFERENCES master_products(id)
);

CREATE INDEX IF NOT EXISTS idx_opp_history_product
    ON opportunity_history (master_product_id, recorded_at DESC);

CREATE INDEX IF NOT EXISTS idx_opp_history_opportunity
    ON opportunity_history (opportunity_id, recorded_at DESC);

-- 2. Lifecycle fields on opportunities table
ALTER TABLE opportunities
    ADD COLUMN IF NOT EXISTS decay_rate REAL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS lifetime_hours REAL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS urgency_score REAL DEFAULT 50,
    ADD COLUMN IF NOT EXISTS lifecycle_label TEXT DEFAULT 'fresh';
