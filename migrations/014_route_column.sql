-- Add route label column to opportunities table
ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS route TEXT DEFAULT '';
-- Add sell_tax column (was missing from earlier migrations)
ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS sell_tax NUMERIC(12,2) DEFAULT 0;
