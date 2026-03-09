-- ============================================================
-- Migration 003: Production-grade arbitrage engine
-- Adds: listing metadata, price history, enhanced opportunities
-- ============================================================

-- 1. Enhanced product_listings: seller/condition/reviews metadata
ALTER TABLE product_listings
    ADD COLUMN IF NOT EXISTS condition TEXT DEFAULT 'new',
    ADD COLUMN IF NOT EXISTS seller_name TEXT,
    ADD COLUMN IF NOT EXISTS seller_rating REAL,
    ADD COLUMN IF NOT EXISTS reviews_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS sales_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS stock_available INTEGER,
    ADD COLUMN IF NOT EXISTS is_free_shipping BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS shipping_price NUMERIC(12, 2);

-- 2. Price history for trend detection
CREATE TABLE IF NOT EXISTS price_history (
    id              BIGSERIAL PRIMARY KEY,
    listing_url     TEXT NOT NULL,
    marketplace_id  TEXT NOT NULL,
    price           NUMERIC(12, 2) NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'USD',
    recorded_at     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_price_history_url
    ON price_history (listing_url, recorded_at DESC);

CREATE INDEX IF NOT EXISTS idx_price_history_marketplace
    ON price_history (marketplace_id, recorded_at DESC);

-- 3. Enhanced opportunities: scoring, confidence, competition
ALTER TABLE opportunities
    ADD COLUMN IF NOT EXISTS estimated_sell_price NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS marketplace_fee NUMERIC(12, 2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS payment_fee NUMERIC(12, 2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS import_tax NUMERIC(12, 2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS domestic_shipping NUMERIC(12, 2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS international_shipping NUMERIC(12, 2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS sales_velocity_score REAL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS competition_score REAL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS price_stability_score REAL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS opportunity_score REAL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS confidence_level TEXT DEFAULT 'low',
    ADD COLUMN IF NOT EXISTS competitor_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS avg_market_price NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS lowest_competitor_price NUMERIC(12, 2);

-- 4. Index for opportunity scoring queries
CREATE INDEX IF NOT EXISTS idx_opportunities_score
    ON opportunities (opportunity_score DESC)
    WHERE status = 'active';

-- 5. Unique constraint: one active opportunity per master_product + direction
CREATE UNIQUE INDEX IF NOT EXISTS idx_opportunities_unique_active
    ON opportunities (master_product_id, buy_marketplace, sell_marketplace)
    WHERE status = 'active';
