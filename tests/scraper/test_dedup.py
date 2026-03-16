"""
Unit Tests — Deduplication Filter.

Tests:
  - Hash determinism
  - Local dedup within a batch
  - Cross-batch dedup
  - Reset behavior
  - Case/whitespace normalization
"""
import pytest

from scraper.dedup import DedupFilter, listing_hash, _normalize_title
from scraper.schemas import RawListing


def _make_listing(title="Test Product", price=99.99, marketplace="ebay"):
    return RawListing(
        title=title,
        price=price,
        currency="USD",
        url="https://example.com/1",
        marketplace_id=marketplace,
    )


class TestListingHash:

    def test_deterministic(self):
        listing = _make_listing()
        assert listing_hash(listing) == listing_hash(listing)

    def test_different_title_different_hash(self):
        l1 = _make_listing(title="Product A")
        l2 = _make_listing(title="Product B")
        assert listing_hash(l1) != listing_hash(l2)

    def test_different_marketplace_different_hash(self):
        l1 = _make_listing(marketplace="ebay")
        l2 = _make_listing(marketplace="amazon")
        assert listing_hash(l1) != listing_hash(l2)

    def test_different_price_different_hash(self):
        l1 = _make_listing(price=99.99)
        l2 = _make_listing(price=100.00)
        assert listing_hash(l1) != listing_hash(l2)

    def test_same_price_same_hash(self):
        """Prices with same 2-decimal value produce same hash."""
        l1 = _make_listing(price=99.99)
        l2 = _make_listing(price=99.990)
        assert listing_hash(l1) == listing_hash(l2)

    def test_case_insensitive(self):
        """Title normalization is case-insensitive."""
        l1 = _make_listing(title="Sony Headphones")
        l2 = _make_listing(title="SONY HEADPHONES")
        assert listing_hash(l1) == listing_hash(l2)

    def test_hash_length(self):
        h = listing_hash(_make_listing())
        assert len(h) == 16  # 16-char hex


class TestNormalizeTitle:

    def test_lowercase(self):
        assert _normalize_title("HELLO WORLD") == "hello world"

    def test_strips_special_chars(self):
        result = _normalize_title("hello! @world#")
        assert result == "hello world"

    def test_collapses_whitespace(self):
        assert _normalize_title("hello    world") == "hello world"

    def test_strips_edges(self):
        assert _normalize_title("  hello  ") == "hello"


class TestDedupFilter:

    @pytest.mark.asyncio
    async def test_first_batch_all_unique(self):
        dedup = DedupFilter(redis_client=None)
        listings = [_make_listing(title=f"Product {i}") for i in range(5)]
        result = await dedup.filter_batch(listings)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_duplicates_within_batch(self):
        dedup = DedupFilter(redis_client=None)
        listings = [
            _make_listing(title="Same Product"),
            _make_listing(title="Same Product"),
            _make_listing(title="Different Product"),
        ]
        result = await dedup.filter_batch(listings)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_duplicates_across_batches(self):
        dedup = DedupFilter(redis_client=None)
        batch1 = [_make_listing(title="Product A")]
        batch2 = [_make_listing(title="Product A"), _make_listing(title="Product B")]

        await dedup.filter_batch(batch1)
        result = await dedup.filter_batch(batch2)
        assert len(result) == 1
        assert result[0].title == "Product B"

    @pytest.mark.asyncio
    async def test_reset_clears_memory(self):
        dedup = DedupFilter(redis_client=None)
        await dedup.filter_batch([_make_listing(title="Product A")])
        assert dedup.local_count == 1

        dedup.reset()
        assert dedup.local_count == 0

        # Same listing should pass again after reset
        result = await dedup.filter_batch([_make_listing(title="Product A")])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_batch(self):
        dedup = DedupFilter(redis_client=None)
        result = await dedup.filter_batch([])
        assert result == []

    @pytest.mark.asyncio
    async def test_case_normalized_dedup(self):
        """Listings with different casing are treated as duplicates."""
        dedup = DedupFilter(redis_client=None)
        listings = [
            _make_listing(title="Sony WH-1000XM4"),
            _make_listing(title="sony wh-1000xm4"),
        ]
        result = await dedup.filter_batch(listings)
        assert len(result) == 1
