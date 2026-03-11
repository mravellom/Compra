-- ============================================================
-- Migration 012: UNIQUE constraint on product_listings.url
-- Prevents duplicate listings from repeated scraper runs.
-- ============================================================

-- Remove duplicates first: keep only the most recent per URL
DELETE FROM product_listings
WHERE id NOT IN (
    SELECT DISTINCT ON (url) id
    FROM product_listings
    ORDER BY url, scraped_at DESC NULLS LAST
);

-- Add unique constraint
ALTER TABLE product_listings
    ADD CONSTRAINT uq_product_listings_url UNIQUE (url);
