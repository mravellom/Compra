-- ============================================================
-- Migration 015: Optimize vector search performance
-- 1. Rebuild HNSW index with better params (m=24, ef_construction=200)
-- 2. Add category index for filtered searches
-- 3. Add materialized median_price_usd on master_products to avoid
--    fetching all listings for price compatibility checks
-- 4. Add index on product_listings for fast median aggregation
-- ============================================================

-- 1. Rebuild HNSW index with better construction quality
--    m=24: more connections per node → better recall
--    ef_construction=200: higher build quality → faster searches
DROP INDEX IF EXISTS idx_master_products_embedding;
CREATE INDEX idx_master_products_embedding
    ON master_products
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 24, ef_construction = 200);

-- 2. Materialized median price for fast price compatibility checks
--    Avoids SELECT * FROM product_listings WHERE master_product_id = ?
--    on every single match attempt.
ALTER TABLE master_products
    ADD COLUMN IF NOT EXISTS median_price_usd NUMERIC(12, 2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS listing_price_count INTEGER DEFAULT 0;

-- 3. Index for fast price aggregation by master product
CREATE INDEX IF NOT EXISTS idx_product_listings_master_price
    ON product_listings (master_product_id, price, currency);

-- 4. Partial index on brand for brand-filtered vector searches
--    Covers the common case where brand IS NOT NULL
CREATE INDEX IF NOT EXISTS idx_master_products_brand
    ON master_products (brand)
    WHERE brand IS NOT NULL;

-- 5. Set HNSW search quality parameter (session-level default)
--    Higher ef_search = better recall at slight cost of latency.
--    Default is 40; 100 is a good balance for <100k products.
ALTER DATABASE compraventa SET hnsw.ef_search = 100;
