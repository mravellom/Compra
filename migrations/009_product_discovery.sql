-- ============================================================
-- Migration 009: Product Discovery & Trending
-- Tracks product velocity, discovery timestamps, and trend scoring
-- ============================================================

-- Snapshot table: one row per product per time window
CREATE TABLE IF NOT EXISTS product_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    master_product_id BIGINT NOT NULL REFERENCES master_products(id),
    snapshot_date   DATE NOT NULL DEFAULT CURRENT_DATE,
    listing_count   INTEGER NOT NULL DEFAULT 0,
    marketplace_count INTEGER NOT NULL DEFAULT 0,
    seller_count    INTEGER NOT NULL DEFAULT 0,
    avg_price       NUMERIC(12,2) DEFAULT 0,
    min_price       NUMERIC(12,2) DEFAULT 0,
    max_price       NUMERIC(12,2) DEFAULT 0,
    total_reviews   INTEGER DEFAULT 0,
    total_sales     INTEGER DEFAULT 0,
    UNIQUE (master_product_id, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_product_date
    ON product_snapshots (master_product_id, snapshot_date DESC);

CREATE INDEX IF NOT EXISTS idx_snapshots_date
    ON product_snapshots (snapshot_date DESC);

-- Add discovery metadata to master_products
ALTER TABLE master_products
    ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS listing_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS marketplace_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS seller_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS avg_price NUMERIC(12,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS trend_score REAL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS trend_label TEXT DEFAULT 'new',
    ADD COLUMN IF NOT EXISTS velocity_7d REAL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS velocity_30d REAL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS is_trending BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS last_snapshot_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_master_products_trending
    ON master_products (is_trending, trend_score DESC)
    WHERE is_trending = TRUE;

CREATE INDEX IF NOT EXISTS idx_master_products_first_seen
    ON master_products (first_seen_at DESC);
