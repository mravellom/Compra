-- ============================================================
-- Migration 006: Category-level arbitrage analytics
-- Aggregates opportunity data by product category
-- ============================================================

-- Materialized stats table, refreshed on each scan
CREATE TABLE IF NOT EXISTS category_arbitrage_stats (
    id              BIGSERIAL PRIMARY KEY,
    category_name   TEXT NOT NULL UNIQUE,
    total_products  INTEGER NOT NULL DEFAULT 0,
    opportunity_count INTEGER NOT NULL DEFAULT 0,
    avg_profit      NUMERIC(12, 2) NOT NULL DEFAULT 0,
    avg_roi         REAL NOT NULL DEFAULT 0,
    avg_sales_velocity REAL NOT NULL DEFAULT 0,
    avg_competition REAL NOT NULL DEFAULT 0,
    avg_demand_trend REAL NOT NULL DEFAULT 50,
    best_roi        REAL NOT NULL DEFAULT 0,
    best_profit     NUMERIC(12, 2) NOT NULL DEFAULT 0,
    category_score  REAL NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_category_stats_score
    ON category_arbitrage_stats (category_score DESC);

-- Ensure master_products.category has an index for grouping
CREATE INDEX IF NOT EXISTS idx_master_products_category
    ON master_products (category)
    WHERE category IS NOT NULL;
