"""
Anti-blocking scraping infrastructure.

Provides:
- Rotating proxy pool with health tracking
- Randomized user agents (desktop + mobile)
- Request delays with jitter
- CAPTCHA and block detection
- Session rotation (fresh httpx clients)
- Enhanced retry with exponential backoff
"""
import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# ── User Agent Pool ─────────────────────────────────────────

DESKTOP_USER_AGENTS = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Chrome Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Safari
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]

ACCEPT_LANGUAGES = [
    "es-MX,es;q=0.9,en;q=0.8",
    "es-AR,es;q=0.9,en;q=0.8",
    "es-ES,es;q=0.9,en;q=0.8",
    "en-US,en;q=0.9,es;q=0.8",
    "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
]


def random_headers(
    accept_language: str | None = None,
    skip_accept_encoding: bool = False,
) -> dict[str, str]:
    """Generate randomized browser-like headers."""
    ua = random.choice(DESKTOP_USER_AGENTS)
    lang = accept_language or random.choice(ACCEPT_LANGUAGES)
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": lang,
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    if not skip_accept_encoding:
        headers["Accept-Encoding"] = "gzip, deflate, br"
    return headers


# ── Proxy Pool ──────────────────────────────────────────────

@dataclass
class ProxyEntry:
    url: str
    fail_count: int = 0
    last_used: float = 0
    cooldown_until: float = 0


class ProxyPool:
    """
    Rotating proxy pool with health tracking.

    Load proxies from PROXY_URLS env var (comma-separated) or pass a list.
    Format: http://user:pass@host:port or http://host:port
    """

    MAX_FAILS = 3
    COOLDOWN_SECONDS = 300  # 5 min cooldown after max fails

    def __init__(self, proxy_urls: list[str] | None = None):
        urls = proxy_urls
        if urls is None:
            env_val = os.getenv("PROXY_URLS", "").strip()
            urls = [u.strip() for u in env_val.split(",") if u.strip()] if env_val else []

        self._proxies: list[ProxyEntry] = [ProxyEntry(url=u) for u in urls]
        self._index = 0
        self._lock = asyncio.Lock()

    @property
    def has_proxies(self) -> bool:
        return len(self._proxies) > 0

    @property
    def size(self) -> int:
        return len(self._proxies)

    async def get_proxy(self) -> str | None:
        """Get next healthy proxy URL (round-robin). Returns None if no proxies available."""
        if not self._proxies:
            return None

        async with self._lock:
            now = time.monotonic()
            # Try all proxies once looking for a healthy one
            for _ in range(len(self._proxies)):
                entry = self._proxies[self._index]
                self._index = (self._index + 1) % len(self._proxies)

                if entry.cooldown_until > now:
                    continue  # Still in cooldown

                entry.last_used = now
                return entry.url

            # All in cooldown — return the one with earliest cooldown end
            earliest = min(self._proxies, key=lambda p: p.cooldown_until)
            wait = earliest.cooldown_until - now
            if wait > 0:
                logger.warning("All proxies in cooldown, waiting %.1fs", wait)
                await asyncio.sleep(wait)
            earliest.last_used = time.monotonic()
            return earliest.url

    async def report_success(self, proxy_url: str) -> None:
        """Reset fail count on success."""
        async with self._lock:
            for entry in self._proxies:
                if entry.url == proxy_url:
                    entry.fail_count = max(0, entry.fail_count - 1)
                    break

    async def report_failure(self, proxy_url: str) -> None:
        """Increment fail count, apply cooldown if threshold reached."""
        async with self._lock:
            for entry in self._proxies:
                if entry.url == proxy_url:
                    entry.fail_count += 1
                    if entry.fail_count >= self.MAX_FAILS:
                        entry.cooldown_until = time.monotonic() + self.COOLDOWN_SECONDS
                        entry.fail_count = 0
                        logger.warning(
                            "Proxy %s in cooldown for %ds",
                            _mask_proxy(proxy_url), self.COOLDOWN_SECONDS,
                        )
                    break


def _mask_proxy(url: str) -> str:
    """Mask proxy credentials for logging."""
    parsed = urlparse(url)
    if parsed.username:
        return f"{parsed.scheme}://***@{parsed.hostname}:{parsed.port}"
    return url


# ── Block Detection ─────────────────────────────────────────

@dataclass
class BlockDetectionResult:
    is_blocked: bool = False
    block_type: str = ""  # "captcha", "rate_limit", "access_denied", "bot_check"
    message: str = ""


def detect_block(response: httpx.Response) -> BlockDetectionResult:
    """Detect if a response indicates we're being blocked."""
    status = response.status_code

    # HTTP status-based detection
    if status == 429:
        return BlockDetectionResult(
            is_blocked=True, block_type="rate_limit",
            message=f"429 Too Many Requests",
        )
    if status == 403:
        return BlockDetectionResult(
            is_blocked=True, block_type="access_denied",
            message="403 Forbidden",
        )
    if status == 503:
        # Could be WAF or legitimate service unavailable
        body_lower = response.text[:2000].lower()
        if any(kw in body_lower for kw in ("captcha", "challenge", "cloudflare", "bot")):
            return BlockDetectionResult(
                is_blocked=True, block_type="bot_check",
                message="503 with bot challenge detected",
            )

    # Content-based detection (only check first 5KB for performance)
    if status == 200:
        body_lower = response.text[:5000].lower()

        # CAPTCHA indicators
        captcha_signals = [
            "captcha", "recaptcha", "hcaptcha", "funcaptcha",
            "robot", "i'm not a robot", "no soy un robot",
            "verify you are a human", "verifica que eres humano",
        ]
        if any(signal in body_lower for signal in captcha_signals):
            # Double-check: parse HTML to avoid false positives from product descriptions
            soup = BeautifulSoup(response.text[:5000], "html.parser")
            title = (soup.title.get_text(strip=True).lower() if soup.title else "")
            form_captcha = soup.select_one("form[action*='captcha'], #captcha, .captcha")
            if form_captcha or "captcha" in title or "robot" in title:
                return BlockDetectionResult(
                    is_blocked=True, block_type="captcha",
                    message="CAPTCHA page detected",
                )

        # Amazon bot check
        if "to discuss automated access" in body_lower or "sorry, we just need to make sure" in body_lower:
            return BlockDetectionResult(
                is_blocked=True, block_type="bot_check",
                message="Amazon bot verification page",
            )

        # MercadoLibre access restriction
        if "acceso restringido" in body_lower or "access denied" in body_lower:
            return BlockDetectionResult(
                is_blocked=True, block_type="access_denied",
                message="Access denied page",
            )

        # Empty/suspiciously small response for search pages
        if len(response.text) < 1000:
            return BlockDetectionResult(
                is_blocked=True, block_type="bot_check",
                message=f"Suspiciously small response ({len(response.text)} bytes)",
            )

    return BlockDetectionResult(is_blocked=False)


# ── Jitter Delay ────────────────────────────────────────────

async def jitter_delay(base: float = 1.0, factor: float = 0.5) -> None:
    """Sleep for base ± (base * factor) seconds. Adds randomness to request timing."""
    low = base * (1 - factor)
    high = base * (1 + factor)
    delay = random.uniform(low, high)
    await asyncio.sleep(delay)


# ── Stealth Session ─────────────────────────────────────────

class StealthSession:
    """
    A managed httpx session with:
    - Automatic proxy rotation
    - Random user agent per request
    - Block detection and proxy failover
    - Jitter between requests
    - Session rotation after N requests
    """

    REQUESTS_PER_SESSION = 15  # Rotate session after this many requests
    BASE_DELAY = 2.0  # Base delay between requests (seconds)
    JITTER_FACTOR = 0.5  # ± 50% jitter

    def __init__(
        self,
        proxy_pool: ProxyPool | None = None,
        rate_limiter: RateLimiter | None = None,
        accept_language: str | None = None,
        base_delay: float | None = None,
        max_retries: int = 3,
        skip_accept_encoding: bool = False,
    ):
        self._proxy_pool = proxy_pool or ProxyPool()
        self._rate_limiter = rate_limiter or RateLimiter(requests_per_second=0.5, burst=2)
        self._accept_language = accept_language
        self._base_delay = base_delay if base_delay is not None else self.BASE_DELAY
        self._max_retries = max_retries
        self._skip_accept_encoding = skip_accept_encoding
        self._client: httpx.AsyncClient | None = None
        self._request_count = 0
        self._current_proxy: str | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create an httpx client, rotating after N requests."""
        if (
            self._client is None
            or self._request_count >= self.REQUESTS_PER_SESSION
        ):
            await self._rotate_session()
        return self._client

    async def _rotate_session(self) -> None:
        """Create a fresh httpx client with new proxy and headers."""
        if self._client:
            await self._client.aclose()

        self._current_proxy = await self._proxy_pool.get_proxy() if self._proxy_pool.has_proxies else None
        headers = random_headers(self._accept_language, skip_accept_encoding=self._skip_accept_encoding)

        proxy_arg = self._current_proxy if self._current_proxy else None
        self._client = httpx.AsyncClient(
            headers=headers,
            proxy=proxy_arg,
            follow_redirects=True,
            timeout=30,
        )
        self._request_count = 0

        if self._current_proxy:
            logger.debug("Session rotated with proxy %s", _mask_proxy(self._current_proxy))
        else:
            logger.debug("Session rotated (no proxy)")

    async def fetch(
        self,
        url: str,
        *,
        domain: str = "default",
    ) -> httpx.Response:
        """
        Fetch a URL with full anti-blocking:
        1. Rate limiting
        2. Jitter delay
        3. Random headers
        4. Proxy rotation on block
        5. Block detection with retry
        """
        last_exc: Exception | None = None

        for attempt in range(self._max_retries):
            # Rate limit
            await self._rate_limiter.acquire(domain)

            # Jitter delay (skip on first attempt if rate limiter already waited)
            if attempt > 0:
                backoff = self._base_delay * (2 ** attempt)
                await jitter_delay(backoff, self.JITTER_FACTOR)
            else:
                await jitter_delay(self._base_delay * 0.5, self.JITTER_FACTOR)

            client = await self._get_client()

            try:
                resp = await client.get(url, timeout=30)
                self._request_count += 1
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                last_exc = exc
                logger.warning(
                    "Request failed (attempt %d/%d) %s: %s",
                    attempt + 1, self._max_retries, url[:80], exc,
                )
                if self._current_proxy:
                    await self._proxy_pool.report_failure(self._current_proxy)
                # Force session rotation for next attempt
                self._request_count = self.REQUESTS_PER_SESSION
                continue

            # Check for blocks
            block = detect_block(resp)
            if block.is_blocked:
                logger.warning(
                    "Block detected [%s] (attempt %d/%d) on %s: %s",
                    block.block_type, attempt + 1, self._max_retries,
                    url[:80], block.message,
                )

                if self._current_proxy:
                    await self._proxy_pool.report_failure(self._current_proxy)

                # Force session rotation (new proxy + headers)
                self._request_count = self.REQUESTS_PER_SESSION

                if block.block_type == "rate_limit":
                    retry_after = float(resp.headers.get("Retry-After", 10))
                    await asyncio.sleep(retry_after)
                continue

            # Success
            if self._current_proxy:
                await self._proxy_pool.report_success(self._current_proxy)
            return resp

        # All retries exhausted
        if last_exc:
            raise last_exc
        raise RuntimeError(f"Blocked after {self._max_retries} retries: {url}")

    async def close(self) -> None:
        """Close the underlying httpx client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
