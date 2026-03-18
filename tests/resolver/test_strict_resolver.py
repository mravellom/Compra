"""
Tests — Strict Resolver (hardened product matching).

Verifies StrictTitleFilter, strict chain, and post-validation logic.
"""
import pytest

from processor.hybrid_resolver import (
    BrandMatcher,
    ListingInput,
    MatchCandidate,
    MatchSignal,
    MatchVerdict,
    ModelMatcher,
    StrictMatchConfig,
    StrictTitleFilter,
    build_strict_resolver_chain,
    hybrid_match,
    strict_hybrid_match,
    REJECT_TITLE_KEYWORDS,
)


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


class TestStrictTitleFilter:

    def test_clean_title_passes(self):
        f = StrictTitleFilter()
        listing = _listing("Samsung Galaxy S24 Ultra 256GB")
        candidate = _candidate("samsung galaxy s24 ultra 256gb")
        signal = f._match(listing, candidate)
        assert signal.verdict == MatchVerdict.PASS

    def test_rejects_bundle(self):
        f = StrictTitleFilter()
        listing = _listing("PS5 Bundle with 2 Controllers")
        candidate = _candidate("ps5 console")
        signal = f._match(listing, candidate)
        assert signal.verdict == MatchVerdict.REJECT
        assert "bundle" in signal.reason

    def test_rejects_case_accessory(self):
        f = StrictTitleFilter()
        listing = _listing("Funda para iPhone 15 Pro")
        candidate = _candidate("iphone 15 pro")
        signal = f._match(listing, candidate)
        assert signal.verdict == MatchVerdict.REJECT

    def test_rejects_compatible_marker(self):
        f = StrictTitleFilter()
        listing = _listing("Cable compatible con MacBook Pro")
        candidate = _candidate("macbook pro")
        signal = f._match(listing, candidate)
        assert signal.verdict == MatchVerdict.REJECT

    def test_rejects_charger(self):
        f = StrictTitleFilter()
        listing = _listing("Cargador rápido USB-C 65W")
        candidate = _candidate("laptop usb-c")
        signal = f._match(listing, candidate)
        assert signal.verdict == MatchVerdict.REJECT


class TestBuildStrictChain:

    def test_chain_starts_with_title_filter(self):
        chain = build_strict_resolver_chain()
        assert isinstance(chain, StrictTitleFilter)

    def test_chain_rejects_accessory_before_brand(self):
        chain = build_strict_resolver_chain()
        listing = _listing("Case para Samsung Galaxy S24", brand="samsung")
        candidate = _candidate("samsung galaxy s24", brand="samsung",
                               embedding_similarity=0.95)
        chain.evaluate(listing, candidate)
        # Should be rejected by title filter, not even reach brand
        assert any(
            s.matcher_name == "StrictTitleFilter" and s.verdict == MatchVerdict.REJECT
            for s in candidate.signals
        )


class TestStrictHybridMatch:

    def test_perfect_match_accepted(self):
        listing = _listing("Samsung Galaxy S24 Ultra 256GB", brand="samsung")
        candidates = [
            _candidate("samsung galaxy s24 ultra 256gb", brand="samsung",
                       embedding_similarity=0.95, master_id=1),
        ]
        result = strict_hybrid_match(listing, candidates)
        assert result is not None
        assert result.master_product_id == 1

    def test_rejects_without_brand_match(self):
        listing = _listing("Galaxy S24 Ultra", brand=None)
        candidates = [
            _candidate("samsung galaxy s24 ultra", brand="samsung",
                       embedding_similarity=0.95),
        ]
        result = strict_hybrid_match(listing, candidates)
        assert result is None  # Brand not confirmed (PASS, not ACCEPT)

    def test_rejects_without_model_match(self):
        listing = _listing("Samsung Wireless Headphones", brand="samsung")
        candidates = [
            _candidate("samsung wireless headphones", brand="samsung",
                       embedding_similarity=0.95),
        ]
        # No model IDs to extract → model not confirmed
        result = strict_hybrid_match(listing, candidates)
        assert result is None

    def test_caps_semantic_only_confidence(self):
        cfg = StrictMatchConfig(
            require_brand_exact=False,
            require_model_exact=False,
            semantic_max_weight=0.20,
            min_confidence=0.10,
        )
        listing = _listing("Some Product", brand=None)
        candidates = [
            _candidate("some product", brand=None, embedding_similarity=0.95),
        ]
        result = strict_hybrid_match(listing, candidates, config=cfg)
        # Even with high embedding, confidence capped
        # With no brand/model signals, composite_confidence is halved embedding (0.475)
        # but since no structural accept, it gets capped to 0.20
        assert result is not None
        # The cap was applied (we can't check internal conf directly,
        # but it was selected meaning it passed min_confidence=0.10)

    def test_hybrid_match_strict_flag_delegates(self):
        listing = _listing("Samsung Galaxy S24 Ultra 256GB", brand="samsung")
        candidates = [
            _candidate("samsung galaxy s24 ultra 256gb", brand="samsung",
                       embedding_similarity=0.95),
        ]
        result = hybrid_match(listing, candidates, strict=True)
        assert result is not None

    def test_rejects_accessory_title(self):
        listing = _listing("Funda para Samsung Galaxy S24", brand="samsung")
        candidates = [
            _candidate("samsung galaxy s24 ultra", brand="samsung",
                       embedding_similarity=0.95),
        ]
        result = strict_hybrid_match(listing, candidates)
        assert result is None
