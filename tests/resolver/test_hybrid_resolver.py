"""
Unit Tests — Hybrid Resolver (Chain of Responsibility).

Tests the full chain: BrandMatcher → ModelMatcher → AttributeMatcher → SemanticMatcher

Scenarios:
  - Identical products match with high confidence
  - Different models rejected (Galaxy A11 vs A16)
  - Brand mismatch rejected
  - Storage variant mismatch rejected
  - Bundle mismatch rejected
  - Semantic fallback when attributes missing
  - Confidence scoring aggregation
  - Brand aliases (HP = Hewlett-Packard)
"""
import pytest

from processor.hybrid_resolver import (
    BrandMatcher,
    ModelMatcher,
    AttributeMatcher,
    SemanticMatcher,
    MatchCandidate,
    MatchSignal,
    MatchVerdict,
    ListingInput,
    build_resolver_chain,
    hybrid_match,
)


# ── Helpers ──────────────────────────────────────────────────


def _listing(
    title: str,
    brand: str | None = None,
    model: str | None = None,
    price_usd: float = 100.0,
) -> ListingInput:
    return ListingInput(
        title=title,
        normalized_title=title.lower(),
        brand=brand,
        model=model,
        category=None,
        price_usd=price_usd,
        currency="USD",
        marketplace_id="test",
    )


def _candidate(
    name: str,
    brand: str | None = None,
    model: str | None = None,
    embedding_similarity: float = 0.90,
    master_id: int = 1,
) -> MatchCandidate:
    return MatchCandidate(
        master_product_id=master_id,
        canonical_name=name.lower(),
        brand=brand,
        model=model,
        embedding_similarity=embedding_similarity,
    )


# ═══════════════════════════════════════════════════════════════
# 1. BrandMatcher Tests
# ═══════════════════════════════════════════════════════════════


class TestBrandMatcher:

    def test_same_brand_accepts(self):
        matcher = BrandMatcher()
        listing = _listing("Galaxy S24 Ultra", brand="samsung")
        candidate = _candidate("galaxy s24 ultra 256gb", brand="samsung")
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.ACCEPT
        assert signal.confidence == 0.6

    def test_different_brand_rejects(self):
        matcher = BrandMatcher()
        listing = _listing("Galaxy S24", brand="samsung")
        candidate = _candidate("iphone 15", brand="apple")
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.REJECT

    def test_missing_brand_passes(self):
        matcher = BrandMatcher()
        listing = _listing("Wireless Headphones", brand=None)
        candidate = _candidate("sony wh1000xm4", brand="sony")
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.PASS

    def test_both_brands_missing_passes(self):
        matcher = BrandMatcher()
        listing = _listing("Generic Product", brand=None)
        candidate = _candidate("generic product", brand=None)
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.PASS

    def test_brand_alias_hp(self):
        matcher = BrandMatcher()
        listing = _listing("HP Laptop", brand="hp")
        candidate = _candidate("hewlett-packard laptop", brand="hewlett-packard")
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.ACCEPT
        assert signal.confidence == 0.55

    def test_case_insensitive(self):
        matcher = BrandMatcher()
        listing = _listing("SONY XM5", brand="Sony")
        candidate = _candidate("sony xm5", brand="sony")
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.ACCEPT


# ═══════════════════════════════════════════════════════════════
# 2. ModelMatcher Tests
# ═══════════════════════════════════════════════════════════════


class TestModelMatcher:

    def test_same_model_accepts(self):
        matcher = ModelMatcher()
        listing = _listing("samsung galaxy s24 ultra 256gb")
        candidate = _candidate("samsung galaxy s24 ultra 256gb")
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.ACCEPT

    def test_different_models_rejects_a11_vs_a16(self):
        """THE core fix: Galaxy A11 vs A16 must be REJECTED."""
        matcher = ModelMatcher()
        listing = _listing("samsung galaxy a11 64gb")
        candidate = _candidate("samsung galaxy a16 128gb")
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.REJECT

    def test_iphone_15_vs_14_rejects(self):
        matcher = ModelMatcher()
        listing = _listing("apple iphone 15 pro max")
        candidate = _candidate("apple iphone 14 pro max")
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.REJECT

    def test_no_model_ids_passes(self):
        matcher = ModelMatcher()
        listing = _listing("wireless headphones bluetooth")
        candidate = _candidate("bluetooth headphones wireless")
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.PASS

    def test_wh1000xm4_variants_match(self):
        matcher = ModelMatcher()
        listing = _listing("sony wh1000xm4 headphones")
        candidate = _candidate("sony wh1000xm4 audifonos")
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.ACCEPT

    def test_airpods_pro_vs_regular(self):
        matcher = ModelMatcher()
        listing = _listing("apple airpods pro 2")
        candidate = _candidate("apple airpods 3rd gen")
        signal = matcher._match(listing, candidate)
        # "airpodspro" vs "airpods" — different model IDs
        assert signal.verdict == MatchVerdict.REJECT


# ═══════════════════════════════════════════════════════════════
# 3. AttributeMatcher Tests
# ═══════════════════════════════════════════════════════════════


class TestAttributeMatcher:

    def test_storage_match_accepts(self):
        matcher = AttributeMatcher()
        listing = _listing("iphone 15 128gb")
        candidate = _candidate("iphone 15 128gb negro")
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.ACCEPT
        assert signal.confidence == 0.7

    def test_storage_mismatch_rejects(self):
        matcher = AttributeMatcher()
        listing = _listing("iphone 15 128gb")
        candidate = _candidate("iphone 15 256gb")
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.REJECT

    def test_bundle_mismatch_rejects(self):
        matcher = AttributeMatcher()
        listing = _listing("ps5 console")
        candidate = _candidate("ps5 console bundle with games")
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.REJECT

    def test_no_attributes_passes(self):
        matcher = AttributeMatcher()
        listing = _listing("wireless mouse")
        candidate = _candidate("wireless mouse ergonomic")
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.PASS

    def test_1tb_vs_2tb_rejects(self):
        matcher = AttributeMatcher()
        listing = _listing("ssd 1tb nvme")
        candidate = _candidate("ssd 2tb nvme")
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.REJECT


# ═══════════════════════════════════════════════════════════════
# 4. SemanticMatcher Tests
# ═══════════════════════════════════════════════════════════════


class TestSemanticMatcher:

    def test_high_similarity_accepts(self):
        matcher = SemanticMatcher(threshold=0.82)
        listing = _listing("test")
        candidate = _candidate("test", embedding_similarity=0.95)
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.ACCEPT
        assert signal.confidence > 0.5

    def test_below_threshold_rejects(self):
        matcher = SemanticMatcher(threshold=0.82)
        listing = _listing("test")
        candidate = _candidate("test", embedding_similarity=0.50)
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.REJECT

    def test_borderline_passes(self):
        matcher = SemanticMatcher(threshold=0.82)
        listing = _listing("test")
        # 0.82 * 0.85 ≈ 0.697 — between reject and accept
        candidate = _candidate("test", embedding_similarity=0.75)
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.PASS

    def test_exact_threshold_accepts(self):
        matcher = SemanticMatcher(threshold=0.82)
        listing = _listing("test")
        candidate = _candidate("test", embedding_similarity=0.82)
        signal = matcher._match(listing, candidate)
        assert signal.verdict == MatchVerdict.ACCEPT

    def test_confidence_scales_with_excess(self):
        matcher = SemanticMatcher(threshold=0.82)
        listing = _listing("test")

        c1 = _candidate("low", embedding_similarity=0.85)
        c2 = _candidate("high", embedding_similarity=0.98)

        s1 = matcher._match(listing, c1)
        s2 = matcher._match(listing, c2)
        assert s2.confidence > s1.confidence


# ═══════════════════════════════════════════════════════════════
# 5. Full Chain Tests (hybrid_match)
# ═══════════════════════════════════════════════════════════════


class TestHybridMatch:

    def test_perfect_match(self):
        listing = _listing("samsung galaxy s24 ultra 256gb", brand="samsung")
        candidates = [
            _candidate("samsung galaxy s24 ultra 256gb", brand="samsung",
                       embedding_similarity=0.95, master_id=1),
        ]
        result = hybrid_match(listing, candidates)
        assert result is not None
        assert result.master_product_id == 1
        assert result.composite_confidence > 0.5

    def test_brand_mismatch_rejects(self):
        listing = _listing("samsung galaxy s24", brand="samsung")
        candidates = [
            _candidate("apple iphone 15", brand="apple",
                       embedding_similarity=0.95, master_id=1),
        ]
        result = hybrid_match(listing, candidates)
        assert result is None  # Rejected by BrandMatcher

    def test_model_mismatch_rejects(self):
        listing = _listing("samsung galaxy a11 64gb", brand="samsung")
        candidates = [
            _candidate("samsung galaxy a16 128gb", brand="samsung",
                       embedding_similarity=0.92, master_id=1),
        ]
        result = hybrid_match(listing, candidates)
        assert result is None  # Rejected by ModelMatcher

    def test_selects_best_candidate(self):
        listing = _listing("sony wh1000xm4 negro", brand="sony")
        candidates = [
            _candidate("sony wh1000xm4 blanco", brand="sony",
                       embedding_similarity=0.88, master_id=1),
            _candidate("sony wh1000xm4 audifonos negro", brand="sony",
                       embedding_similarity=0.95, master_id=2),
        ]
        result = hybrid_match(listing, candidates)
        assert result is not None
        # Both match, but candidate 2 has higher embedding sim
        assert result.master_product_id == 2

    def test_no_candidates_returns_none(self):
        listing = _listing("test product")
        result = hybrid_match(listing, [])
        assert result is None

    def test_all_below_min_confidence_returns_none(self):
        listing = _listing("product a", brand="x")
        candidates = [
            _candidate("product z", brand="y",  # Brand mismatch → confidence 0
                       embedding_similarity=0.95, master_id=1),
        ]
        result = hybrid_match(listing, candidates, min_confidence=0.45)
        assert result is None


class TestCompositeConfidence:

    def test_reject_zeroes_confidence(self):
        candidate = _candidate("test", brand="apple")
        candidate.signals = [
            MatchSignal(MatchVerdict.ACCEPT, 0.8, "good", "BrandMatcher"),
            MatchSignal(MatchVerdict.REJECT, 0.0, "bad", "ModelMatcher"),
        ]
        assert candidate.composite_confidence == 0.0

    def test_accept_averages(self):
        candidate = _candidate("test")
        candidate.signals = [
            MatchSignal(MatchVerdict.ACCEPT, 0.6, "brand", "BrandMatcher"),
            MatchSignal(MatchVerdict.ACCEPT, 0.9, "model", "ModelMatcher"),
        ]
        assert candidate.composite_confidence == pytest.approx(0.75, abs=0.01)

    def test_only_pass_halves_embedding(self):
        candidate = _candidate("test", embedding_similarity=0.90)
        candidate.signals = [
            MatchSignal(MatchVerdict.PASS, 0.0, "no info", "BrandMatcher"),
        ]
        assert candidate.composite_confidence == pytest.approx(0.45, abs=0.01)

    def test_no_signals_zero(self):
        candidate = _candidate("test")
        candidate.signals = []
        assert candidate.composite_confidence == 0.0
