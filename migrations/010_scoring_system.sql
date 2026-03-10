-- 010: Add risk_score and confidence_score columns to opportunities
-- Replaces text-only confidence_level with numeric confidence_score (0-100)
-- Adds risk_score (0-100) for downside assessment

ALTER TABLE opportunities
    ADD COLUMN IF NOT EXISTS risk_score REAL DEFAULT 50,
    ADD COLUMN IF NOT EXISTS confidence_score REAL DEFAULT 50;

-- Backfill from existing data
UPDATE opportunities SET
    confidence_score = CASE confidence_level
        WHEN 'high' THEN 80
        WHEN 'medium' THEN 55
        WHEN 'low' THEN 25
        ELSE 50
    END,
    risk_score = GREATEST(0, LEAST(100,
        -- Higher competition = higher risk
        (competition_score * 0.3) +
        -- Lower stability = higher risk
        ((100 - COALESCE(price_stability_score, 50)) * 0.3) +
        -- Lower depth = higher risk
        ((100 - COALESCE(market_depth_score, 50)) * 0.2) +
        -- Very high ROI = suspicious = higher risk
        (LEAST(100, GREATEST(0, (roi - 0.5) * 200)) * 0.2)
    ))
WHERE risk_score = 50 AND confidence_score = 50;

-- Index for sorting/filtering by the three scores
CREATE INDEX IF NOT EXISTS idx_opportunities_scores
    ON opportunities (opportunity_score DESC, risk_score ASC, confidence_score DESC)
    WHERE status = 'active';
