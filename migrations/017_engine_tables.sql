-- Migration 017: Price Prediction, Trend Detection, and Execution Engine tables
-- Run: psql -U postgres -d compraventa -f migrations/017_engine_tables.sql

BEGIN;

-- ============================================================
-- PRICE PREDICTION ENGINE
-- ============================================================

CREATE TABLE IF NOT EXISTS price_predictions (
    id              BIGSERIAL PRIMARY KEY,
    product_id      BIGINT NOT NULL REFERENCES master_products(id) ON DELETE CASCADE,
    model_type      TEXT NOT NULL DEFAULT 'baseline',       -- baseline, prophet, xgboost
    horizon_days    INTEGER NOT NULL DEFAULT 7,             -- 7, 14, 30
    predicted_price NUMERIC(12,2) NOT NULL,
    confidence_lower NUMERIC(12,2),
    confidence_upper NUMERIC(12,2),
    mape            REAL,                                   -- mean absolute percentage error
    confidence      REAL DEFAULT 0.5,                       -- 0-1 overall confidence
    features_used   JSONB DEFAULT '{}',                     -- feature importances / metadata
    status          TEXT NOT NULL DEFAULT 'computed',        -- computed, stale, failed
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_prediction_product_model_horizon
        UNIQUE (product_id, model_type, horizon_days)
);

CREATE INDEX IF NOT EXISTS idx_predictions_product ON price_predictions(product_id);
CREATE INDEX IF NOT EXISTS idx_predictions_created ON price_predictions(created_at DESC);

-- ============================================================
-- TREND DETECTION ENGINE
-- ============================================================

CREATE TABLE IF NOT EXISTS product_trends (
    id              BIGSERIAL PRIMARY KEY,
    product_id      BIGINT NOT NULL REFERENCES master_products(id) ON DELETE CASCADE,
    trend_score     REAL NOT NULL DEFAULT 0,                -- composite 0-100
    velocity_ratio  REAL DEFAULT 0,                         -- 7d vs 30d velocity ratio
    price_momentum  REAL DEFAULT 0,                         -- price rate of change
    volume_change   REAL DEFAULT 0,                         -- listing volume delta
    trend_type      TEXT NOT NULL DEFAULT 'stable',         -- rising, falling, stable, breakout
    trend_strength  TEXT NOT NULL DEFAULT 'weak',           -- weak, moderate, strong
    signals         JSONB DEFAULT '{}',                     -- per-strategy signal breakdown
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_trend_product UNIQUE (product_id)
);

CREATE INDEX IF NOT EXISTS idx_trends_score ON product_trends(trend_score DESC);
CREATE INDEX IF NOT EXISTS idx_trends_type ON product_trends(trend_type);
CREATE INDEX IF NOT EXISTS idx_trends_detected ON product_trends(detected_at DESC);

-- ============================================================
-- EXECUTION ENGINE
-- ============================================================

CREATE TABLE IF NOT EXISTS execution_orders (
    id              BIGSERIAL PRIMARY KEY,
    opportunity_id  BIGINT REFERENCES opportunities(id) ON DELETE SET NULL,
    product_id      BIGINT NOT NULL REFERENCES master_products(id) ON DELETE CASCADE,
    order_type      TEXT NOT NULL,                          -- buy, sell, list_item
    marketplace     TEXT NOT NULL,
    price           NUMERIC(12,2) NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 1,
    total_cost      NUMERIC(12,2) NOT NULL DEFAULT 0,
    estimated_profit NUMERIC(12,2) DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'draft',          -- draft, pending_approval, approved, executing, executed, failed, cancelled
    approval_state  TEXT NOT NULL DEFAULT 'pending',        -- pending, approved, rejected, auto_approved
    execution_mode  TEXT NOT NULL DEFAULT 'manual',         -- manual, assisted, auto
    risk_assessment JSONB DEFAULT '{}',
    error_message   TEXT,
    approved_by     TEXT,
    approved_at     TIMESTAMPTZ,
    executed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_orders_status ON execution_orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_product ON execution_orders(product_id);
CREATE INDEX IF NOT EXISTS idx_orders_opportunity ON execution_orders(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_orders_created ON execution_orders(created_at DESC);

CREATE TABLE IF NOT EXISTS execution_log (
    id              BIGSERIAL PRIMARY KEY,
    order_id        BIGINT NOT NULL REFERENCES execution_orders(id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL,                          -- created, submitted, approved, rejected, executing, executed, failed, cancelled
    old_status      TEXT,
    new_status      TEXT,
    details         JSONB DEFAULT '{}',
    actor           TEXT DEFAULT 'system',                  -- system, user, auto
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_exec_log_order ON execution_log(order_id);
CREATE INDEX IF NOT EXISTS idx_exec_log_created ON execution_log(created_at DESC);

COMMIT;
