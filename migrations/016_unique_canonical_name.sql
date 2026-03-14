-- ============================================================
-- Migration 016: Add UNIQUE constraint on canonical_name
-- Enables ON CONFLICT (canonical_name) in resolver for safe
-- concurrent product creation without blocking advisory locks.
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS idx_master_products_canonical_name
    ON master_products (canonical_name);
