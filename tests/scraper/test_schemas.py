"""
Unit Tests — RawListing Schema.

Tests:
  - Default values
  - to_stream_dict serialization
  - Validation
"""
import pytest
from datetime import datetime, timezone

from scraper.schemas import RawListing


class TestRawListing:

    def test_minimal_creation(self):
        listing = RawListing(
            title="Test Product",
            price=29.99,
            url="https://example.com/1",
            marketplace_id="ebay",
        )
        assert listing.title == "Test Product"
        assert listing.price == 29.99
        assert listing.currency == "USD"
        assert listing.condition == "new"
        assert listing.reviews_count == 0
        assert listing.is_free_shipping is False

    def test_full_creation(self):
        listing = RawListing(
            title="Sony Headphones",
            price=229.99,
            currency="MXN",
            url="https://amazon.com.mx/dp/123",
            marketplace_id="amazon",
            condition="refurbished",
            seller_rating=4.8,
            reviews_count=500,
            sales_count=200,
            is_free_shipping=True,
        )
        assert listing.seller_rating == 4.8
        assert listing.sales_count == 200

    def test_scraped_at_auto_set(self):
        listing = RawListing(
            title="Test",
            price=10.0,
            url="https://example.com/1",
            marketplace_id="test",
        )
        assert listing.scraped_at is not None
        assert isinstance(listing.scraped_at, datetime)

    def test_to_stream_dict_all_strings(self):
        listing = RawListing(
            title="Test",
            price=29.99,
            url="https://example.com/1",
            marketplace_id="ebay",
            seller_rating=4.5,
        )
        d = listing.to_stream_dict()
        assert isinstance(d, dict)
        for k, v in d.items():
            assert isinstance(v, str), f"Key {k} has non-string value: {type(v)}"

    def test_to_stream_dict_none_becomes_empty(self):
        listing = RawListing(
            title="Test",
            price=10.0,
            url="https://example.com/1",
            marketplace_id="test",
            image_url=None,
            seller_name=None,
        )
        d = listing.to_stream_dict()
        assert d.get("image_url", "") == ""
        assert d.get("seller_name", "") == ""
