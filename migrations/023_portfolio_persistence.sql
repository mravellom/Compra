-- Portfolio state persistence: crash-safe capital management
-- Replaces in-memory singleton with DB-backed state + advisory locks

CREATE TABLE IF NOT EXISTS portfolio_state (
    id BIGSERIAL PRIMARY KEY,
    strategy_mode TEXT NOT NULL DEFAULT 'conservative',
    total_capital NUMERIC(12,2) NOT NULL DEFAULT 5000.00,
    available_capital NUMERIC(12,2) NOT NULL DEFAULT 5000.00,
    allocated_capital NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    reserved_capital NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    realized_profit NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    unrealized_profit NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    peak_capital NUMERIC(12,2) NOT NULL DEFAULT 5000.00,
    drawdown_pct REAL NOT NULL DEFAULT 0.0,
    max_drawdown_pct REAL NOT NULL DEFAULT 0.0,
    consecutive_losses INTEGER NOT NULL DEFAULT 0,
    total_trades INTEGER NOT NULL DEFAULT 0,
    winning_trades INTEGER NOT NULL DEFAULT 0,
    losing_trades INTEGER NOT NULL DEFAULT 0,
    daily_loss_usd NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    daily_loss_reset_date DATE,
    last_loss_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS portfolio_positions (
    id BIGSERIAL PRIMARY KEY,
    portfolio_id BIGINT NOT NULL REFERENCES portfolio_state(id) ON DELETE CASCADE,
    opportunity_id BIGINT REFERENCES opportunities(id) ON DELETE SET NULL,
    product_id BIGINT,
    allocated_amount NUMERIC(12,2) NOT NULL,
    entry_price NUMERIC(12,2) NOT NULL,
    expected_profit NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    expected_roi REAL NOT NULL DEFAULT 0.0,
    risk_score REAL NOT NULL DEFAULT 50.0,
    status TEXT NOT NULL DEFAULT 'open',
    marketplace TEXT,
    category TEXT,
    exit_price NUMERIC(12,2),
    actual_profit NUMERIC(12,2),
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_positions_portfolio_status
    ON portfolio_positions(portfolio_id, status);
CREATE INDEX IF NOT EXISTS idx_positions_opportunity
    ON portfolio_positions(opportunity_id);
-- Prevent duplicate open positions for the same opportunity
CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_opp_open_unique
    ON portfolio_positions(opportunity_id)
    WHERE status = 'open';

-- Seed a single portfolio row (singleton pattern in DB)
INSERT INTO portfolio_state (id, strategy_mode, total_capital, available_capital, peak_capital)
VALUES (1, 'conservative', 5000.00, 5000.00, 5000.00)
ON CONFLICT (id) DO NOTHING;

-- Add unique partial index to prevent duplicate active opportunities
CREATE UNIQUE INDEX IF NOT EXISTS idx_opportunities_active_unique
    ON opportunities(master_product_id, buy_marketplace, sell_marketplace)
    WHERE status = 'active';
