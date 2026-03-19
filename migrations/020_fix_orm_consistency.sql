-- ============================================================
-- Migration 020: Fix ORM-DB consistency
-- 1. Add FK on opportunity_history.opportunity_id -> opportunities.id
-- 2. No-op comments documenting ORM-only fixes applied in this batch
-- ============================================================

-- 1. Add missing FK constraint on opportunity_history.opportunity_id
--    The column existed since migration 007 but without a FK.
--    Safe: only adds constraint, does not modify data.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_opp_history_opportunity'
          AND table_name = 'opportunity_history'
    ) THEN
        ALTER TABLE opportunity_history
            ADD CONSTRAINT fk_opp_history_opportunity
            FOREIGN KEY (opportunity_id) REFERENCES opportunities(id);
    END IF;
END
$$;

-- ORM-only fixes applied in api/models.py (no DB changes needed):
-- - Added median_price_usd, listing_price_count to MasterProduct model (columns exist since migration 015)
-- - Added unique=True to MasterProduct.canonical_name (index exists since migration 016)
-- - Added unique=True to ProductListing.url (constraint exists since migration 012)
-- - Added ProductSnapshot model (table exists since migration 009)
-- - Added AlertSent model (table exists since migration 002)
-- - Added OrchestratorPipeline model (table exists since migration 018)
-- - Fixed JSONB columns: features_used, signals, risk_assessment, details (JSONB in DB, was Text in ORM)
