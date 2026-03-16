"""Configuration — loads settings from environment variables.

Single source of truth for all scraper_v2 configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))


def _env_float(key: str, default: float = 0.0) -> float:
    return float(os.getenv(key, str(default)))


def _env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


def _env_list(key: str, default: str = "") -> list[str]:
    val = os.getenv(key, default)
    return [x.strip() for x in val.split(",") if x.strip()] if val else []


@dataclass(frozen=True)
class ScraperV2Config:
    """All scraper_v2 settings from environment."""

    # Redis
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://localhost:6379/0"))
    stream_key: str = field(default_factory=lambda: _env("STREAM_KEY", "raw_listings_queue"))

    # Scraping
    scrape_interval: float = field(default_factory=lambda: _env_float("SCRAPE_INTERVAL", 300.0))
    worker_count: int = field(default_factory=lambda: _env_int("SCRAPER_WORKERS", 10))
    max_tasks_per_cycle: int = field(default_factory=lambda: _env_int("MAX_TASKS_PER_CYCLE", 500))

    # Rate limiting
    default_rps: float = field(default_factory=lambda: _env_float("DEFAULT_RPS", 2.0))
    min_rps: float = field(default_factory=lambda: _env_float("MIN_RPS", 0.1))
    max_rps: float = field(default_factory=lambda: _env_float("MAX_RPS", 10.0))

    # Proxy
    proxy_urls: list[str] = field(default_factory=lambda: _env_list("PROXY_URLS"))
    proxy_cooldown: float = field(default_factory=lambda: _env_float("PROXY_COOLDOWN", 300.0))

    # Browser
    browser_pool_size: int = field(default_factory=lambda: _env_int("BROWSER_POOL_SIZE", 3))
    browser_headless: bool = field(default_factory=lambda: _env_bool("BROWSER_HEADLESS", True))

    # Circuit breaker
    cb_failure_threshold: int = field(default_factory=lambda: _env_int("CB_FAILURE_THRESHOLD", 5))
    cb_recovery_timeout: float = field(default_factory=lambda: _env_float("CB_RECOVERY_TIMEOUT", 60.0))

    # Bulkhead
    max_concurrent_per_marketplace: int = field(
        default_factory=lambda: _env_int("MAX_CONCURRENT_PER_MP", 4)
    )
    max_concurrent_total: int = field(
        default_factory=lambda: _env_int("MAX_CONCURRENT_TOTAL", 20)
    )

    # Categories
    category_max_pages: int = field(default_factory=lambda: _env_int("CATEGORY_MAX_PAGES", 15))

    # Weights
    weight_category: float = field(default_factory=lambda: _env_float("WEIGHT_CATEGORY", 0.60))
    weight_search: float = field(default_factory=lambda: _env_float("WEIGHT_SEARCH", 0.25))
    weight_trending: float = field(default_factory=lambda: _env_float("WEIGHT_TRENDING", 0.15))

    # Dedup
    dedup_ttl: int = field(default_factory=lambda: _env_int("DEDUP_TTL", 86400))
    dedup_local_cache: int = field(default_factory=lambda: _env_int("DEDUP_LOCAL_CACHE", 50000))

    # Backpressure
    backpressure_threshold: int = field(
        default_factory=lambda: _env_int("BACKPRESSURE_THRESHOLD", 50000)
    )

    # Publish
    publish_batch_size: int = field(default_factory=lambda: _env_int("PUBLISH_BATCH_SIZE", 100))

    # Logging
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))


def load_config() -> ScraperV2Config:
    return ScraperV2Config()
