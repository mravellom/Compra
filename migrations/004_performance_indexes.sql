-- Performance indexes for opportunity detection pipeline
-- These indexes eliminate full table scans in the three hottest query paths.

-- 1. product_listings: scan cycle finds products with listings in 2+ marketplaces
--    Query: WHERE scraped_at > NOW() - INTERVAL '48 hours' GROUP BY master_product_id
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_listings_scraped_product
ON product_listings (scraped_at DESC, master_product_id, marketplace_id);

-- 2. price_history: stability/age computation fetches history by listing URL
--    Query: WHERE listing_url = $1 ORDER BY recorded_at DESC LIMIT 20
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_price_history_url_date
ON price_history (listing_url, recorded_at DESC);

-- 3. opportunity_history: lifecycle engine fetches snapshots per product
--    Query: WHERE master_product_id = $1 ORDER BY recorded_at
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_opp_history_product_date
ON opportunity_history (master_product_id, recorded_at DESC);

-- 4. opportunities: upsert checks for active opps by product+direction
--    Query: WHERE master_product_id = $1 AND buy_marketplace = $2 AND sell_marketplace = $3 AND status = 'active'
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_opportunities_active_product
ON opportunities (master_product_id, buy_marketplace, sell_marketplace)
WHERE status = 'active';
