-- 011: Table partitioning for scale (1M+ listings/day)
--
-- Strategy:
--   product_listings  → LIST partition by marketplace_id
--   price_history     → RANGE partition by month
--   opportunities     → LIST partition by status
--
-- NOTE: This migration should be run during a maintenance window.
-- For existing tables, data must be migrated to partitioned versions.
-- Below creates the partitioned structure alongside existing tables.
-- A separate migration script handles data migration.

-- ─── 1. Partitioned price_history ────────────────────────

CREATE TABLE IF NOT EXISTS price_history_partitioned (
    id          BIGSERIAL,
    listing_url TEXT NOT NULL,
    marketplace_id TEXT NOT NULL,
    price       NUMERIC(12, 2) NOT NULL,
    currency    TEXT DEFAULT 'USD',
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, recorded_at)
) PARTITION BY RANGE (recorded_at);

-- Create partitions for 2026 (extend as needed)
CREATE TABLE IF NOT EXISTS price_history_2026_01
    PARTITION OF price_history_partitioned
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE IF NOT EXISTS price_history_2026_02
    PARTITION OF price_history_partitioned
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE IF NOT EXISTS price_history_2026_03
    PARTITION OF price_history_partitioned
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE IF NOT EXISTS price_history_2026_04
    PARTITION OF price_history_partitioned
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE IF NOT EXISTS price_history_2026_05
    PARTITION OF price_history_partitioned
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE IF NOT EXISTS price_history_2026_06
    PARTITION OF price_history_partitioned
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE IF NOT EXISTS price_history_2026_07
    PARTITION OF price_history_partitioned
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE IF NOT EXISTS price_history_2026_08
    PARTITION OF price_history_partitioned
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE IF NOT EXISTS price_history_2026_09
    PARTITION OF price_history_partitioned
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE IF NOT EXISTS price_history_2026_10
    PARTITION OF price_history_partitioned
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE IF NOT EXISTS price_history_2026_11
    PARTITION OF price_history_partitioned
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE IF NOT EXISTS price_history_2026_12
    PARTITION OF price_history_partitioned
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

-- Indexes on partitioned table
CREATE INDEX IF NOT EXISTS idx_ph_part_url_date
    ON price_history_partitioned (listing_url, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_ph_part_mp_date
    ON price_history_partitioned (marketplace_id, recorded_at DESC);


-- ─── 2. Partitioned product_listings ─────────────────────

CREATE TABLE IF NOT EXISTS product_listings_partitioned (
    id                BIGSERIAL,
    master_product_id BIGINT NOT NULL,
    title             TEXT NOT NULL,
    normalized_title  TEXT NOT NULL,
    price             NUMERIC(12, 2) NOT NULL,
    currency          TEXT DEFAULT 'USD',
    url               TEXT NOT NULL,
    marketplace_id    TEXT NOT NULL,
    image_url         TEXT,
    similarity_score  REAL,
    scraped_at        TIMESTAMPTZ,
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    condition         TEXT DEFAULT 'new',
    seller_name       TEXT,
    seller_rating     REAL,
    reviews_count     INTEGER DEFAULT 0,
    sales_count       INTEGER DEFAULT 0,
    stock_available   INTEGER,
    is_free_shipping  BOOLEAN DEFAULT FALSE,
    shipping_price    NUMERIC(12, 2),
    PRIMARY KEY (id, marketplace_id)
) PARTITION BY LIST (marketplace_id);

CREATE TABLE IF NOT EXISTS product_listings_amazon
    PARTITION OF product_listings_partitioned
    FOR VALUES IN ('amazon');
CREATE TABLE IF NOT EXISTS product_listings_ml_ar
    PARTITION OF product_listings_partitioned
    FOR VALUES IN ('mercadolibre_ar');
CREATE TABLE IF NOT EXISTS product_listings_ml_mx
    PARTITION OF product_listings_partitioned
    FOR VALUES IN ('mercadolibre_mx');
CREATE TABLE IF NOT EXISTS product_listings_ebay
    PARTITION OF product_listings_partitioned
    FOR VALUES IN ('ebay');
-- Add new marketplace partitions as needed:
-- CREATE TABLE product_listings_walmart
--     PARTITION OF product_listings_partitioned
--     FOR VALUES IN ('walmart');

-- Indexes per partition (auto-created on partitions)
CREATE INDEX IF NOT EXISTS idx_plp_master_mp
    ON product_listings_partitioned (master_product_id, marketplace_id, price);
CREATE INDEX IF NOT EXISTS idx_plp_url
    ON product_listings_partitioned (url);


-- ─── 3. Data retention helper ────────────────────────────
-- Function to drop old monthly partitions (run via cron)

CREATE OR REPLACE FUNCTION drop_old_partitions(
    parent_table TEXT,
    months_to_keep INTEGER DEFAULT 6
) RETURNS INTEGER AS $$
DECLARE
    cutoff DATE;
    partition_name TEXT;
    dropped INTEGER := 0;
BEGIN
    cutoff := DATE_TRUNC('month', NOW()) - (months_to_keep || ' months')::INTERVAL;

    FOR partition_name IN
        SELECT inhrelid::regclass::text
        FROM pg_inherits
        WHERE inhparent = parent_table::regclass
        ORDER BY inhrelid::regclass::text
    LOOP
        -- Extract date from partition name (format: table_YYYY_MM)
        IF partition_name ~ '_\d{4}_\d{2}$' THEN
            DECLARE
                part_date DATE;
            BEGIN
                part_date := TO_DATE(
                    RIGHT(partition_name, 7), 'YYYY_MM'
                );
                IF part_date < cutoff THEN
                    EXECUTE 'DROP TABLE IF EXISTS ' || partition_name;
                    dropped := dropped + 1;
                    RAISE NOTICE 'Dropped partition: %', partition_name;
                END IF;
            EXCEPTION WHEN OTHERS THEN
                RAISE NOTICE 'Could not parse date from: %', partition_name;
            END;
        END IF;
    END LOOP;

    RETURN dropped;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION drop_old_partitions IS
    'Drop monthly partitions older than N months. Usage: SELECT drop_old_partitions(''price_history_partitioned'', 6);';
