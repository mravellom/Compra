"""Factory — assembles the full scraper_v2 object graph.

Dependency injection via constructor wiring.
All infrastructure adapters are created here and injected into application services.
"""

from __future__ import annotations

from scraper_v2.config import ScraperV2Config
from scraper_v2.domain.enums import Marketplace

# Application layer
from scraper_v2.application.resilience.bulkhead import Bulkhead, BulkheadConfig
from scraper_v2.application.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
)
from scraper_v2.application.resilience.retry import RetryConfig, RetryPolicy
from scraper_v2.application.scheduler.category_priority import (
    CategoryPriorityStrategy,
    PriorityConfig,
)
from scraper_v2.application.scheduler.rate_limiter import (
    AdaptiveRateLimiter,
    RateLimitConfig,
)
from scraper_v2.application.scheduler.request_scheduler import (
    RequestScheduler,
    SchedulerConfig,
)
from scraper_v2.application.services.health_service import HealthService
from scraper_v2.application.services.scraping_service import (
    ScrapingService,
    ScrapingServiceConfig,
)
from scraper_v2.application.strategies.anti_blocking import (
    BrowserFingerprintStrategy,
    JitterDelayConfig,
    JitterDelayStrategy,
    RandomHeaderStrategy,
)

# Infrastructure layer
from scraper_v2.infrastructure.browser.browser_pool import BrowserPool, BrowserPoolConfig
from scraper_v2.infrastructure.http.block_detector import HttpBlockDetector
from scraper_v2.infrastructure.http.proxy_pool import ProxyPool, ProxyPoolConfig
from scraper_v2.infrastructure.http.stealth_client import StealthHttpClient
from scraper_v2.infrastructure.parsers.aliexpress import AliExpressParser
from scraper_v2.infrastructure.parsers.amazon import AmazonParser
from scraper_v2.infrastructure.parsers.base import CompositeParser
from scraper_v2.infrastructure.parsers.ebay import EbayParser
from scraper_v2.infrastructure.parsers.mercadolibre import MercadoLibreParser
from scraper_v2.infrastructure.redis.dedup import RedisDedup
from scraper_v2.infrastructure.redis.producer import RedisStreamProducer
from scraper_v2.infrastructure.redis.task_queue import InMemoryTaskQueue
from scraper_v2.infrastructure.workers.metrics_collector import LoggingMetrics
from scraper_v2.infrastructure.workers.orchestrator import ScraperOrchestrator


def _build_category_urls() -> dict[Marketplace, dict[str, str]]:
    """Import and transform category config from v1 scraper."""
    try:
        from scraper.category_config import get_category_url, DEFAULT_CATEGORIES
        urls: dict[Marketplace, dict[str, str]] = {}
        mp_map = {
            "mercadolibre_ar": Marketplace.MERCADOLIBRE_AR,
            "mercadolibre_mx": Marketplace.MERCADOLIBRE_MX,
            "mercadolibre_cl": Marketplace.MERCADOLIBRE_CL,
            "mercadolibre_co": Marketplace.MERCADOLIBRE_CO,
            "amazon": Marketplace.AMAZON_MX,
            "amazon_us": Marketplace.AMAZON_US,
        }
        for mp_id, mp_enum in mp_map.items():
            mp_urls: dict[str, str] = {}
            for cat in DEFAULT_CATEGORIES:
                url = get_category_url(mp_id, cat)
                if url:
                    mp_urls[cat] = url
            if mp_urls:
                urls[mp_enum] = mp_urls
        return urls
    except ImportError:
        return {}


def _build_search_terms() -> list[str]:
    """Import search terms from v1 scraper."""
    try:
        from scraper.keyword_expander import HIGH_VALUE_TERMS
        return list(HIGH_VALUE_TERMS)
    except ImportError:
        return [
            "airpods pro", "iphone 15", "nintendo switch",
            "sony wh-1000xm5", "samsung galaxy", "macbook air",
        ]


def _build_trending_urls() -> dict[Marketplace, list[str]]:
    return {
        Marketplace.AMAZON_MX: [
            "https://www.amazon.com.mx/gp/bestsellers/electronics",
            "https://www.amazon.com.mx/gp/bestsellers/computers",
        ],
        Marketplace.AMAZON_US: [
            "https://www.amazon.com/gp/bestsellers/electronics",
            "https://www.amazon.com/gp/bestsellers/computers",
        ],
        Marketplace.MERCADOLIBRE_AR: [
            "https://www.mercadolibre.com.ar/ofertas/tecnologia",
        ],
        Marketplace.MERCADOLIBRE_MX: [
            "https://www.mercadolibre.com.mx/ofertas/tecnologia",
        ],
    }


def build_parser() -> CompositeParser:
    """Build the composite parser with all marketplace strategies."""
    parser = CompositeParser()
    parser.register(Marketplace.MERCADOLIBRE_AR, MercadoLibreParser(Marketplace.MERCADOLIBRE_AR))
    parser.register(Marketplace.MERCADOLIBRE_MX, MercadoLibreParser(Marketplace.MERCADOLIBRE_MX))
    parser.register(Marketplace.MERCADOLIBRE_CL, MercadoLibreParser(Marketplace.MERCADOLIBRE_CL))
    parser.register(Marketplace.MERCADOLIBRE_CO, MercadoLibreParser(Marketplace.MERCADOLIBRE_CO))
    parser.register(Marketplace.AMAZON_MX, AmazonParser(Marketplace.AMAZON_MX))
    parser.register(Marketplace.AMAZON_US, AmazonParser(Marketplace.AMAZON_US))
    parser.register(Marketplace.EBAY, EbayParser())
    parser.register(Marketplace.ALIEXPRESS, AliExpressParser())
    return parser


def build_orchestrator(config: ScraperV2Config) -> ScraperOrchestrator:
    """Assemble the full object graph from configuration."""

    # ── Infrastructure adapters ────────────────────────────────
    rate_limiter = AdaptiveRateLimiter(
        RateLimitConfig(
            default_rps=config.default_rps,
            min_rps=config.min_rps,
            max_rps=config.max_rps,
        )
    )

    proxy_pool = ProxyPool.from_urls(
        config.proxy_urls,
        ProxyPoolConfig(cooldown_seconds=config.proxy_cooldown),
    )

    circuit_breaker = CircuitBreaker(
        CircuitBreakerConfig(
            failure_threshold=config.cb_failure_threshold,
            recovery_timeout=config.cb_recovery_timeout,
        )
    )

    bulkhead = Bulkhead(
        BulkheadConfig(
            max_concurrent_per_marketplace=config.max_concurrent_per_marketplace,
            max_concurrent_total=config.max_concurrent_total,
        )
    )

    http_client = StealthHttpClient(
        header_strategy=RandomHeaderStrategy(),
    )

    browser_pool = BrowserPool(
        fingerprint_strategy=BrowserFingerprintStrategy(),
        config=BrowserPoolConfig(
            pool_size=config.browser_pool_size,
            headless=config.browser_headless,
        ),
    )

    parser = build_parser()
    block_detector = HttpBlockDetector()

    dedup = RedisDedup(
        redis_url=config.redis_url,
        ttl_seconds=config.dedup_ttl,
        local_cache_size=config.dedup_local_cache,
    )

    producer = RedisStreamProducer(
        redis_url=config.redis_url,
        stream_key=config.stream_key,
        backpressure_threshold=config.backpressure_threshold,
        batch_pipeline_size=config.publish_batch_size,
    )

    task_queue = InMemoryTaskQueue()
    metrics = LoggingMetrics()

    retry_policy = RetryPolicy(RetryConfig(max_attempts=3, base_delay=2.0))
    delay_strategy = JitterDelayStrategy(JitterDelayConfig(base=2.0, factor=0.5))

    # ── Application services ──────────────────────────────────
    category_priority = CategoryPriorityStrategy(
        PriorityConfig(default_pages=config.category_max_pages)
    )

    category_urls = _build_category_urls()
    search_terms = _build_search_terms()
    trending_urls = _build_trending_urls()

    scheduler = RequestScheduler(
        task_queue=task_queue,
        rate_limiter=rate_limiter,
        category_priority=category_priority,
        category_urls=category_urls,
        search_terms=search_terms,
        trending_urls=trending_urls,
        config=SchedulerConfig(
            weight_category=config.weight_category,
            weight_search=config.weight_search,
            weight_trending=config.weight_trending,
            max_tasks_per_cycle=config.max_tasks_per_cycle,
        ),
    )

    scraping_service = ScrapingService(
        task_queue=task_queue,
        http_client=http_client,
        browser_client=browser_pool,
        parser=parser,
        proxy_pool=proxy_pool,
        block_detector=block_detector,
        dedup=dedup,
        producer=producer,
        rate_limiter=rate_limiter,
        circuit_breaker=circuit_breaker,
        bulkhead=bulkhead,
        delay_strategy=delay_strategy,
        retry_policy=retry_policy,
        metrics=metrics,
        config=ScrapingServiceConfig(
            worker_count=config.worker_count,
            publish_batch_size=config.publish_batch_size,
        ),
    )

    # ── Active marketplaces ───────────────────────────────────
    marketplaces = [mp for mp in category_urls.keys()]
    if not marketplaces:
        marketplaces = [
            Marketplace.MERCADOLIBRE_AR,
            Marketplace.MERCADOLIBRE_MX,
            Marketplace.AMAZON_MX,
            Marketplace.AMAZON_US,
        ]

    return ScraperOrchestrator(
        scheduler=scheduler,
        scraping_service=scraping_service,
        dedup=dedup,
        marketplaces=marketplaces,
        cycle_interval=config.scrape_interval,
    )


def build_health_service(config: ScraperV2Config) -> HealthService:
    """Build health service for API monitoring."""
    # Lightweight — shares references with the orchestrator in practice
    rate_limiter = AdaptiveRateLimiter()
    proxy_pool = ProxyPool.from_urls(config.proxy_urls)
    circuit_breaker = CircuitBreaker()
    bulkhead = Bulkhead()

    return HealthService(
        proxy_pool=proxy_pool,
        circuit_breaker=circuit_breaker,
        rate_limiter=rate_limiter,
        bulkhead=bulkhead,
    )
