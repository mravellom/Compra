"""Ports (interfaces) — hexagonal architecture boundaries.

These abstract base classes define the contracts between layers.
Domain code depends ONLY on these ports, never on concrete implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from .enums import Marketplace
from .models import (
    BlockDetection,
    CrawlResult,
    CrawlTask,
    ProxyEndpoint,
    ProxyHealth,
    RawListing,
    ScraperMetrics,
    WorkerState,
)


# ── Outbound Ports (driven) ───────────────────────────────────


class HttpClientPort(ABC):
    """Port for making HTTP requests with stealth capabilities."""

    @abstractmethod
    async def fetch(
        self,
        url: str,
        *,
        marketplace: Marketplace,
        proxy: ProxyEndpoint | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> tuple[int, str, dict[str, str]]:
        """Fetch URL, return (status_code, body, response_headers)."""

    @abstractmethod
    async def close(self) -> None: ...


class BrowserClientPort(ABC):
    """Port for browser-rendered page fetching."""

    @abstractmethod
    async def fetch_rendered(
        self,
        url: str,
        *,
        marketplace: Marketplace,
        wait_selector: str | None = None,
        scroll: bool = False,
        timeout: float = 45.0,
    ) -> tuple[str, str]:
        """Fetch rendered page, return (html, final_url)."""

    @abstractmethod
    async def close(self) -> None: ...


class ListingParserPort(ABC):
    """Port for parsing HTML/JSON into domain listings."""

    @abstractmethod
    def parse(
        self,
        html: str,
        marketplace: Marketplace,
        source_url: str,
    ) -> list[RawListing]:
        """Parse raw HTML into listing objects."""

    @abstractmethod
    def extract_next_page(self, html: str, marketplace: Marketplace) -> str | None:
        """Extract next page URL from HTML."""


class ProxyPoolPort(ABC):
    """Port for proxy management."""

    @abstractmethod
    async def acquire(self, marketplace: Marketplace | None = None) -> ProxyEndpoint | None:
        """Get next available proxy, optionally filtered by marketplace geo."""

    @abstractmethod
    async def release(self, proxy: ProxyEndpoint, success: bool, latency_ms: float = 0.0) -> None:
        """Return proxy with usage result."""

    @abstractmethod
    async def health_check(self) -> list[ProxyHealth]: ...

    @abstractmethod
    def healthy_count(self) -> int: ...


class BlockDetectorPort(ABC):
    """Port for detecting anti-bot blocks."""

    @abstractmethod
    def detect(self, status_code: int, body: str, headers: dict[str, str]) -> BlockDetection: ...


class StreamProducerPort(ABC):
    """Port for publishing listings to the processing pipeline."""

    @abstractmethod
    async def publish_batch(self, listings: list[RawListing]) -> int:
        """Publish batch, return count actually published."""

    @abstractmethod
    async def check_backpressure(self) -> bool:
        """Return True if downstream is under pressure."""

    @abstractmethod
    async def close(self) -> None: ...


class DedupPort(ABC):
    """Port for deduplication."""

    @abstractmethod
    async def filter_new(self, listings: list[RawListing]) -> list[RawListing]:
        """Return only listings not seen before."""

    @abstractmethod
    async def reset(self) -> None: ...


class MetricsPort(ABC):
    """Port for metrics collection."""

    @abstractmethod
    def increment(self, metric: str, value: float = 1.0, tags: dict[str, str] | None = None) -> None: ...

    @abstractmethod
    def gauge(self, metric: str, value: float, tags: dict[str, str] | None = None) -> None: ...

    @abstractmethod
    def timing(self, metric: str, ms: float, tags: dict[str, str] | None = None) -> None: ...


# ── Inbound Ports (driving) ───────────────────────────────────


class TaskQueuePort(ABC):
    """Port for the crawl task queue."""

    @abstractmethod
    async def enqueue(self, task: CrawlTask) -> None: ...

    @abstractmethod
    async def dequeue(self, timeout: float = 5.0) -> CrawlTask | None: ...

    @abstractmethod
    async def size(self) -> int: ...

    @abstractmethod
    async def drain(self) -> list[CrawlTask]: ...


class ScraperOrchestratorPort(ABC):
    """Port for the top-level orchestrator."""

    @abstractmethod
    async def run_cycle(self) -> ScraperMetrics: ...

    @abstractmethod
    async def shutdown(self) -> None: ...
