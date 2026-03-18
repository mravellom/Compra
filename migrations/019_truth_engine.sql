-- Migration 019: Truth Engine — Trade Outcomes table
-- Stores ground truth for every executed trade to measure real performance.

CREATE TABLE IF NOT EXISTS trade_outcomes (
    id              BIGSERIAL PRIMARY KEY,
    opportunity_id  BIGINT NOT NULL REFERENCES opportunities(id),
    master_product_id BIGINT REFERENCES master_products(id),
    detected_at     TIMESTAMPTZ NOT NULL,
    executed_at     TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,

    -- Predicted values
    buy_price_predicted   NUMERIC(12,2) DEFAULT 0,
    sell_price_predicted  NUMERIC(12,2) DEFAULT 0,
    estimated_profit      NUMERIC(12,2) DEFAULT 0,
    expected_roi          REAL DEFAULT 0,

    -- Actual values
    buy_price_actual      NUMERIC(12,2) DEFAULT 0,
    sell_price_actual     NUMERIC(12,2) DEFAULT 0,
    actual_profit         NUMERIC(12,2) DEFAULT 0,
    actual_roi            REAL DEFAULT 0,

    -- Time tracking
    time_to_sell_minutes  REAL,

    -- Status
    sold                  BOOLEAN DEFAULT FALSE,
    cancelled             BOOLEAN DEFAULT FALSE,
    failure_reason        TEXT,

    -- Route
    buy_marketplace       TEXT DEFAULT '',
    sell_marketplace      TEXT DEFAULT '',

    created_at            TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_trade_outcomes_opportunity ON trade_outcomes(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_trade_outcomes_product ON trade_outcomes(master_product_id);
CREATE INDEX IF NOT EXISTS idx_trade_outcomes_detected ON trade_outcomes(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_trade_outcomes_executed ON trade_outcomes(executed_at) WHERE executed_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trade_outcomes_sold ON trade_outcomes(sold) WHERE sold = TRUE;
