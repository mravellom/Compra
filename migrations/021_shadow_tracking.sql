-- Shadow tracking: records every opportunity evaluation for accuracy analysis
CREATE TABLE IF NOT EXISTS shadow_results (
    id BIGSERIAL PRIMARY KEY,
    opportunity_id BIGINT REFERENCES opportunities(id) ON DELETE CASCADE,
    projected_profit NUMERIC(12,2),
    projected_roi NUMERIC(8,4),
    buy_price_usd NUMERIC(12,2),
    sell_price_usd NUMERIC(12,2),
    validation_passed BOOLEAN,
    rejection_reasons TEXT[],
    scoring_snapshot JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_shadow_created ON shadow_results(created_at);
