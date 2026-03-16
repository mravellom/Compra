"""Composite Listing Parser — delegates to marketplace-specific parsers.

Implements the Strategy Pattern: each marketplace has its own parser strategy,
and the composite parser routes based on the marketplace enum.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from scraper_v2.domain.enums import Marketplace
from scraper_v2.domain.models import RawListing
from scraper_v2.domain.ports import ListingParserPort

logger = logging.getLogger(__name__)


class MarketplaceParser(ABC):
    """Base class for marketplace-specific parsing strategies."""

    @abstractmethod
    def parse_listings(self, html: str, source_url: str) -> list[RawListing]: ...

    @abstractmethod
    def extract_next_page_url(self, html: str) -> str | None: ...


class CompositeParser(ListingParserPort):
    """Routes parsing to marketplace-specific implementations."""

    def __init__(self) -> None:
        self._parsers: dict[Marketplace, MarketplaceParser] = {}

    def register(self, marketplace: Marketplace, parser: MarketplaceParser) -> None:
        self._parsers[marketplace] = parser

    def parse(
        self,
        html: str,
        marketplace: Marketplace,
        source_url: str,
    ) -> list[RawListing]:
        parser = self._parsers.get(marketplace)
        if not parser:
            logger.warning("No parser registered for %s", marketplace.value)
            return []
        try:
            return parser.parse_listings(html, source_url)
        except Exception as exc:
            logger.error(
                "Parser error for %s: %s",
                marketplace.value,
                str(exc)[:200],
            )
            return []

    def extract_next_page(self, html: str, marketplace: Marketplace) -> str | None:
        parser = self._parsers.get(marketplace)
        if not parser:
            return None
        try:
            return parser.extract_next_page_url(html)
        except Exception:
            return None
