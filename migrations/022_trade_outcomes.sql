-- Trade outcomes: tracks actual vs projected profit for accuracy measurement
CREATE TABLE IF NOT EXISTS trade_outcomes (
    id BIGSERIAL PRIMARY KEY,
    opportunity_id BIGINT REFERENCES opportunities(id) ON DELETE SET NULL,
    actual_buy_price NUMERIC(12,2),
    actual_sell_price NUMERIC(12,2),
    actual_fees NUMERIC(12,2),
    actual_profit NUMERIC(12,2),
    projected_profit NUMERIC(12,2),
    deviation_pct NUMERIC(8,4),
    status TEXT DEFAULT 'pending',
    executed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
