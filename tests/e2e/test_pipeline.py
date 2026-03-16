"""
End-to-End Pipeline Test.

Simulates the full pipeline flow without external dependencies:
  1. Raw listing arrives (simulated scraper output)
  2. Price validation passes
  3. Title normalization
  4. Embedding generation (mocked)
  5. Hybrid resolver matches or creates product
  6. Opportunity engine detects arbitrage
  7. Scoring system rates the opportunity
  8. Publisher emits event

All external systems (Redis, PostgreSQL, sentence-transformers) are mocked.
The test verifies data flows correctly through each stage.
"""
import pytest
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch

from scraper.schemas import RawListing
from scraper.dedup import DedupFilter, listing_hash
from processor.normalizer import normalize_title, extract_brand, extract_model
from processor.price_anomaly import detect_price_anomaly
from processor.hybrid_resolver import (
    ListingInput, MatchCandidate, hybrid_match, MatchVerdict,
)
from api.opportunity import calculate_profit, ProfitCalc, variants_compatible
from api.scoring import score, ScoringInput, ScoringOutput
from api.scoring_profiles import score_with_profile, RiskClass
from monetization.publisher import (
    OpportunityPublisher, OpportunityEvent, ChannelFilter, DeliveryResult,
    ChannelAdapter,
)


class InMemoryChannel(ChannelAdapter):
    """Captures events for assertion in E2E tests."""

    def __init__(self):
        super().__init__("e2e_capture", ChannelFilter())
        self.events: list[OpportunityEvent] = []

    async def deliver(self, event: OpportunityEvent) -> DeliveryResult:
        self.events.append(event)
        return DeliveryResult(self.name, True)


class TestEndToEndPipeline:
    """Full pipeline simulation: Scraper → Processor → Opportunity → Scoring → Publisher."""

    @pytest.mark.asyncio
    async def test_full_pipeline_flow(self):
        """
        Scenario: Samsung Galaxy S24 Ultra listed on both Amazon MX and
        MercadoLibre MX at different prices. Pipeline should detect an
        arbitrage opportunity.
        """
        # ── Stage 1: Scraper Output ──────────────────────────────
        buy_listing = RawListing(
            title="Samsung Galaxy S24 Ultra 256GB Negro",
            price=50.0,
            currency="USD",
            url="https://www.ebay.com/itm/B0SAMSUNG",
            marketplace_id="ebay",
            condition="new",
            seller_rating=4.5,
            reviews_count=234,
            sales_count=0,
            is_free_shipping=True,
        )

        sell_listing = RawListing(
            title="Samsung Galaxy S24 Ultra 256GB Black",
            price=200.0,
            currency="USD",
            url="https://www.ebay.com/itm/B0SAMSUNG2",
            marketplace_id="amazon_us",
            condition="new",
            seller_rating=4.8,
            reviews_count=567,
            sales_count=200,
            is_free_shipping=True,
        )

        # ── Stage 2: Deduplication ───────────────────────────────
        dedup = DedupFilter(redis_client=None)
        unique = await dedup.filter_batch([buy_listing, sell_listing])
        assert len(unique) == 2, "Both listings should be unique"

        # Verify duplicate detection
        duplicates = await dedup.filter_batch([buy_listing])
        assert len(duplicates) == 0, "Duplicate should be filtered"

        # ── Stage 3: Price Anomaly Detection ─────────────────────
        assert not detect_price_anomaly(50.0, [], "USD"), "Buy price should be valid"
        assert not detect_price_anomaly(200.0, [], "USD"), "Sell price should be valid"
        assert detect_price_anomaly(0.1, [], "USD"), "Invalid USD price rejected"

        # ── Stage 4: Normalization ───────────────────────────────
        buy_norm = normalize_title(buy_listing.title)
        sell_norm = normalize_title(sell_listing.title)

        assert "samsung" in buy_norm
        assert "galaxy" in buy_norm
        assert "s24" in buy_norm
        assert "256gb" in buy_norm

        buy_brand = extract_brand(buy_norm)
        sell_brand = extract_brand(sell_norm)
        assert buy_brand == "samsung"
        assert sell_brand == "samsung"

        buy_model = extract_model(buy_norm, buy_brand)
        sell_model = extract_model(sell_norm, sell_brand)

        # ── Stage 5: Hybrid Resolver ─────────────────────────────
        listing_input = ListingInput(
            title=buy_listing.title,
            normalized_title=buy_norm,
            brand=buy_brand,
            model=buy_model,
            category=None,
            price_usd=50.0,
            currency="USD",
            marketplace_id="ebay",
        )

        candidate = MatchCandidate(
            master_product_id=1,
            canonical_name=sell_norm,
            brand=sell_brand,
            model=sell_model,
            embedding_similarity=0.95,
        )

        match = hybrid_match(listing_input, [candidate])
        assert match is not None, "Same product should match"
        assert match.composite_confidence > 0.45

        # ── Stage 6: Variant Compatibility ───────────────────────
        buy_mock = MagicMock(
            title=buy_listing.title, condition=buy_listing.condition,
        )
        sell_mock = MagicMock(
            title=sell_listing.title, condition=sell_listing.condition,
        )
        assert variants_compatible(buy_mock, sell_mock), "Same variant should be compatible"

        # ── Stage 7: Profit Calculation ──────────────────────────
        buy_usd = 50.0
        sell_usd = 200.0
        profit = calculate_profit(buy_usd, sell_usd, "ebay", "amazon_us")

        assert isinstance(profit, ProfitCalc)
        assert profit.net_profit > 0, "Should be profitable"
        assert profit.roi > 0, "ROI should be positive"
        assert profit.total_fees > 0, "Fees should be applied"

        # ── Stage 8: Scoring ─────────────────────────────────────
        scoring_input = ScoringInput(
            net_profit_usd=profit.net_profit,
            roi=profit.roi,
            margin=profit.margin,
            buy_price_usd=buy_usd,
            competitor_count=5,
            total_listings=10,
            buy_seller_rating=4.5,
            buy_seller_reviews=234,
            sell_seller_rating=4.8,
            sell_seller_reviews=567,
            total_sales_count=200,
            total_reviews_count=801,
            estimated_daily_sales=3.0,
            market_depth_score=70.0,
            price_stability_score=75.0,
            listing_age_days=30.0,
            is_cross_border=False,
            price_spread_pct=0.12,
        )

        result = score(scoring_input)
        assert isinstance(result, ScoringOutput)
        assert 0 <= result.opportunity_score <= 100
        assert 0 <= result.risk_score <= 100
        assert 0 <= result.confidence_score <= 100

        # Also test with profile
        profiled_result, risk_class = score_with_profile(scoring_input, "balanced")
        assert isinstance(risk_class, RiskClass)

        # ── Stage 9: Publisher ───────────────────────────────────
        publisher = OpportunityPublisher()
        capture = InMemoryChannel()
        publisher.register_channel(capture)

        event = OpportunityEvent(
            event_type="new_opportunity",
            opportunity_id=1,
            product_name="Samsung Galaxy S24 Ultra 256GB",
            buy_marketplace="ebay",
            sell_marketplace="amazon_us",
            route="US→US",
            buy_price=buy_usd,
            sell_price=sell_usd,
            net_profit=profit.net_profit,
            roi=profit.roi,
            opportunity_score=result.opportunity_score,
            risk_score=result.risk_score,
            confidence_level=result.confidence_level,
            risk_class=risk_class.value,
            buy_url=str(buy_listing.url),
            sell_url=str(sell_listing.url),
            category="Smartphones",
        )

        results = await publisher.publish(event)

        # ── Assertions ───────────────────────────────────────────
        assert len(results) == 1
        assert results[0].success is True
        assert len(capture.events) == 1

        published = capture.events[0]
        assert published.opportunity_id == 1
        assert published.product_name == "Samsung Galaxy S24 Ultra 256GB"
        assert published.net_profit > 0
        assert published.roi > 0

    @pytest.mark.asyncio
    async def test_pipeline_rejects_mismatched_products(self):
        """Pipeline should not create opportunity for different products."""
        listing_a = ListingInput(
            title="Samsung Galaxy A11 64GB",
            normalized_title="samsung galaxy a11 64gb",
            brand="samsung", model="a11",
            category=None, price_usd=100.0,
            currency="USD", marketplace_id="amazon",
        )
        candidate_a16 = MatchCandidate(
            master_product_id=2,
            canonical_name="samsung galaxy a16 128gb",
            brand="samsung", model="a16",
            embedding_similarity=0.92,
        )

        match = hybrid_match(listing_a, [candidate_a16])
        assert match is None, "A11 should NOT match A16"

    @pytest.mark.asyncio
    async def test_pipeline_rejects_anomalous_prices(self):
        """Anomalous prices should be rejected before entering the pipeline."""
        # A price of $0.01 MXN should fail validation
        assert detect_price_anomaly(0.01, [], "MXN") is True
        # A price of $999,999 USD should fail
        assert detect_price_anomaly(999999.0, [], "USD") is True

    @pytest.mark.asyncio
    async def test_pipeline_handles_cross_border(self):
        """Cross-border routes should include import taxes and shipping."""
        # US → MX route
        profit = calculate_profit(50.0, 150.0, "amazon_us", "mercadolibre_mx")
        assert profit.import_tax > 0, "Should have import tax"
        assert profit.international_shipping > 0, "Should have intl shipping"
        assert profit.net_profit < (150.0 - 50.0), "Fees should reduce profit"
