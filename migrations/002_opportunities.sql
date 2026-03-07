-- Tabla de oportunidades detectadas
CREATE TABLE IF NOT EXISTS opportunities (
    id                  BIGSERIAL PRIMARY KEY,
    master_product_id   BIGINT NOT NULL REFERENCES master_products(id),
    buy_listing_id      BIGINT NOT NULL REFERENCES product_listings(id),
    sell_listing_id     BIGINT NOT NULL REFERENCES product_listings(id),
    buy_price           NUMERIC(12, 2) NOT NULL,
    sell_price          NUMERIC(12, 2) NOT NULL,
    fees                NUMERIC(12, 2) NOT NULL,
    shipping_cost       NUMERIC(12, 2) NOT NULL,
    net_profit          NUMERIC(12, 2) NOT NULL,
    roi                 REAL NOT NULL,
    buy_marketplace     TEXT NOT NULL,
    sell_marketplace    TEXT NOT NULL,
    status              TEXT DEFAULT 'active',  -- active, expired, taken
    created_at          TIMESTAMPTZ DEFAULT now(),
    expired_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_opportunities_roi ON opportunities (roi DESC);
CREATE INDEX IF NOT EXISTS idx_opportunities_status ON opportunities (status);
CREATE INDEX IF NOT EXISTS idx_opportunities_master ON opportunities (master_product_id);
CREATE INDEX IF NOT EXISTS idx_opportunities_created ON opportunities (created_at DESC);

-- Tabla de configuración de alertas por usuario
CREATE TABLE IF NOT EXISTS alert_configs (
    id              BIGSERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL,
    min_roi         REAL DEFAULT 0.20,
    min_profit      NUMERIC(12, 2) DEFAULT 30.00,
    max_buy_price   NUMERIC(12, 2),
    categories      TEXT[],
    marketplaces    TEXT[],
    telegram_chat_id TEXT,
    email           TEXT,
    enabled         BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_configs_user ON alert_configs (user_id);

-- Tabla de alertas enviadas (historial)
CREATE TABLE IF NOT EXISTS alerts_sent (
    id              BIGSERIAL PRIMARY KEY,
    opportunity_id  BIGINT NOT NULL REFERENCES opportunities(id),
    alert_config_id BIGINT NOT NULL REFERENCES alert_configs(id),
    channel         TEXT NOT NULL,  -- telegram, email, dashboard
    sent_at         TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_alerts_sent_opportunity ON alerts_sent (opportunity_id);
