"""Domain enumerations — pure value types, no infrastructure."""

from enum import Enum, auto


class Marketplace(str, Enum):
    MERCADOLIBRE_AR = "mercadolibre_ar"
    MERCADOLIBRE_MX = "mercadolibre_mx"
    MERCADOLIBRE_CL = "mercadolibre_cl"
    MERCADOLIBRE_CO = "mercadolibre_co"
    AMAZON_MX = "amazon_mx"
    AMAZON_US = "amazon_us"
    EBAY = "ebay"
    ALIEXPRESS = "aliexpress"

    @property
    def domain(self) -> str:
        return _MARKETPLACE_DOMAINS[self]

    @property
    def requires_browser(self) -> bool:
        return self in _BROWSER_REQUIRED


class CrawlMode(str, Enum):
    CATEGORY = "category"
    SEARCH = "search"
    TRENDING = "trending"


class WorkerStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    THROTTLED = "throttled"
    BLOCKED = "blocked"
    CRASHED = "crashed"


class ProxyStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DEAD = "dead"
    COOLDOWN = "cooldown"


class BlockType(str, Enum):
    NONE = "none"
    RATE_LIMIT = "rate_limit"
    CAPTCHA = "captcha"
    IP_BAN = "ip_ban"
    WAF = "waf"
    GEO_BLOCK = "geo_block"


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class TaskPriority(int, Enum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


_MARKETPLACE_DOMAINS: dict[Marketplace, str] = {
    Marketplace.MERCADOLIBRE_AR: "listado.mercadolibre.com.ar",
    Marketplace.MERCADOLIBRE_MX: "listado.mercadolibre.com.mx",
    Marketplace.MERCADOLIBRE_CL: "listado.mercadolibre.cl",
    Marketplace.MERCADOLIBRE_CO: "listado.mercadolibre.com.co",
    Marketplace.AMAZON_MX: "www.amazon.com.mx",
    Marketplace.AMAZON_US: "www.amazon.com",
    Marketplace.EBAY: "www.ebay.com",
    Marketplace.ALIEXPRESS: "www.aliexpress.com",
}

_BROWSER_REQUIRED: set[Marketplace] = {
    Marketplace.MERCADOLIBRE_CL,
    Marketplace.MERCADOLIBRE_CO,
    Marketplace.ALIEXPRESS,
}
