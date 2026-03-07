"""
Tests de validación para schemas Pydantic (RawListing).
Verifica serialización, defaults y validation errors.
"""
import pytest
from pydantic import ValidationError

from scraper.schemas import RawListing


class TestRawListing:

    def test_valid_listing_creation(self):
        listing = RawListing(
            title="Sony WH-1000XM4",
            price=229.99,
            currency="USD",
            url="https://www.ebay.com/itm/123456",
            marketplace_id="ebay",
        )
        assert listing.title == "Sony WH-1000XM4"
        assert listing.price == 229.99
        assert listing.scraped_at is not None

    def test_invalid_url_raises_validation_error(self):
        with pytest.raises(ValidationError):
            RawListing(
                title="Test",
                price=10.0,
                url="not-a-url",
                marketplace_id="ebay",
            )

    def test_to_stream_dict_all_values_are_strings(self):
        listing = RawListing(
            title="Test Product",
            price=99.99,
            url="https://www.ebay.com/itm/123",
            marketplace_id="ebay",
        )
        d = listing.to_stream_dict()
        assert all(isinstance(v, str) for v in d.values())
        assert d["price"] == "99.99"
        assert d["marketplace_id"] == "ebay"

    def test_optional_fields_serialize_as_empty_string(self):
        listing = RawListing(
            title="Test",
            price=10.0,
            url="https://www.ebay.com/itm/1",
            marketplace_id="ebay",
        )
        d = listing.to_stream_dict()
        assert d["image_url"] == ""
        assert d["raw_html_snippet"] == ""

    def test_defaults_applied(self):
        listing = RawListing(
            title="Test",
            price=10.0,
            url="https://example.com/1",
            marketplace_id="test",
        )
        assert listing.currency == "USD"
        assert listing.image_url is None
        assert listing.raw_html_snippet is None
