-- ============================================================
-- Migration 013: Reset product data for embedding model change
-- Required after switching from all-MiniLM-L6-v2 to
-- paraphrase-multilingual-MiniLM-L12-v2.
-- Old embeddings are incompatible with the new model.
-- The scraper will re-populate all data on next run.
-- ============================================================

TRUNCATE TABLE opportunities CASCADE;
TRUNCATE TABLE price_history CASCADE;
TRUNCATE TABLE product_listings CASCADE;
TRUNCATE TABLE master_products CASCADE;
