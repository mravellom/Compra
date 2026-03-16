"""Domain models — immutable value objects and entities.

No framework imports. No infrastructure. Pure business logic carriers.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .enums import (
    BlockType,
    CrawlMode,
    Marketplace,
    ProxyStatus,
    TaskPriority,
    WorkerStatus,
)


# ── Value Objects ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RawListing:
    """Immutable scraped listing — the core unit of work."""

    title: str
    price: float
    currency: str
    url: str
    marketplace: Marketplace
    image_url: str = ""
    condition: str = "new"
    seller_name: str = ""
    seller_rating: float = 0.0
    reviews_count: int = 0
    sales_count: int = 0
    is_free_shipping: bool = False
    shipping_price: float = 0.0
    stock_available: int | None = None
    scraped_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def dedup_key(self) -> str:
        normalized = self.title.strip().lower()
        price_rounded = f"{self.price:.0f}"
        raw = f"{normalized}|{self.marketplace.value}|{price_rounded}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def to_stream_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "price": str(self.price),
            "currency": self.currency,
            "url": self.url,
            "marketplace_id": self.marketplace.value,
            "image_url": self.image_url,
            "condition": self.condition,
            "seller_name": self.seller_name,
            "seller_rating": str(self.seller_rating),
            "reviews_count": str(self.reviews_count),
            "sales_count": str(self.sales_count),
            "is_free_shipping": str(self.is_free_shipping),
            "shipping_price": str(self.shipping_price),
            "stock_available": str(self.stock_available or ""),
            "scraped_at": self.scraped_at,
        }


@dataclass(frozen=True, slots=True)
class CrawlTask:
    """A unit of work for a crawler worker."""

    task_id: str
    marketplace: Marketplace
    mode: CrawlMode
    url: str
    category: str = ""
    search_term: str = ""
    page: int = 1
    max_pages: int = 15
    priority: TaskPriority = TaskPriority.NORMAL
    created_at: float = field(default_factory=time.monotonic)

    @property
    def domain(self) -> str:
        return self.marketplace.domain


@dataclass(frozen=True, slots=True)
class ProxyEndpoint:
    """A proxy server endpoint with health metadata."""

    url: str
    region: str = "unknown"
    provider: str = "unknown"

    @property
    def host(self) -> str:
        return self.url.split("@")[-1].split(":")[0] if "@" in self.url else self.url.split("//")[-1].split(":")[0]


@dataclass(slots=True)
class ProxyHealth:
    """Mutable health state for a proxy."""

    proxy: ProxyEndpoint
    status: ProxyStatus = ProxyStatus.HEALTHY
    success_count: int = 0
    failure_count: int = 0
    total_latency_ms: float = 0.0
    last_used: float = 0.0
    cooldown_until: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        total = self.success_count + self.failure_count
        return self.total_latency_ms / total if total > 0 else 0.0

    @property
    def failure_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.failure_count / total if total > 0 else 0.0

    def record_success(self, latency_ms: float) -> None:
        self.success_count += 1
        self.total_latency_ms += latency_ms
        self.last_used = time.monotonic()
        self.status = ProxyStatus.HEALTHY

    def record_failure(self, max_failures: int = 3, cooldown_secs: float = 300.0) -> None:
        self.failure_count += 1
        self.last_used = time.monotonic()
        if self.failure_count >= max_failures:
            self.status = ProxyStatus.COOLDOWN
            self.cooldown_until = time.monotonic() + cooldown_secs


@dataclass(frozen=True, slots=True)
class BlockDetection:
    """Result of block detection analysis."""

    blocked: bool
    block_type: BlockType = BlockType.NONE
    message: str = ""
    retry_after: float = 0.0


@dataclass(frozen=True, slots=True)
class CrawlResult:
    """Output of a single crawl operation."""

    task: CrawlTask
    listings: list[RawListing]
    next_page_url: str | None = None
    blocked: bool = False
    block_type: BlockType = BlockType.NONE
    duration_ms: float = 0.0
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None and not self.blocked


@dataclass(slots=True)
class WorkerState:
    """Runtime state of a crawler worker."""

    worker_id: str
    status: WorkerStatus = WorkerStatus.IDLE
    marketplace: Marketplace | None = None
    tasks_completed: int = 0
    tasks_failed: int = 0
    listings_scraped: int = 0
    current_task: CrawlTask | None = None
    started_at: float = field(default_factory=time.monotonic)
    last_active: float = field(default_factory=time.monotonic)


@dataclass(slots=True)
class CategoryStats:
    """Performance stats for a scraping category."""

    category: str
    total_listings: int = 0
    opportunities_found: int = 0
    avg_roi: float = 0.0
    cycles_scraped: int = 0
    allocated_pages: int = 15
    priority_score: float = 0.5

    @property
    def opportunity_rate(self) -> float:
        return self.opportunities_found / self.total_listings if self.total_listings > 0 else 0.0


@dataclass(frozen=True, slots=True)
class ScraperMetrics:
    """Snapshot of scraper system metrics."""

    active_workers: int = 0
    idle_workers: int = 0
    tasks_queued: int = 0
    tasks_completed: int = 0
    listings_total: int = 0
    listings_per_second: float = 0.0
    blocks_detected: int = 0
    proxies_healthy: int = 0
    proxies_total: int = 0
    circuit_breakers_open: int = 0
    uptime_seconds: float = 0.0
