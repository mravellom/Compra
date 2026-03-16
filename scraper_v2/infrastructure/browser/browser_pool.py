"""Browser Pool — manages multiple Playwright browser instances.

Unlike the v1 singleton browser, this pool supports:
- Multiple concurrent browser contexts
- Session reuse with fingerprint rotation
- Automatic cleanup of stale sessions
- Configurable pool size for horizontal scaling
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from scraper_v2.domain.enums import Marketplace
from scraper_v2.domain.ports import BrowserClientPort
from scraper_v2.application.strategies.anti_blocking import (
    BrowserFingerprintStrategy,
    FingerprintStrategy,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BrowserPoolConfig:
    pool_size: int = 3
    max_pages_per_context: int = 20
    page_timeout: float = 45.0
    navigation_timeout: float = 30.0
    context_ttl: float = 600.0  # 10 min max lifetime per context
    headless: bool = True


@dataclass
class _BrowserContext:
    context: object  # playwright BrowserContext
    pages_used: int = 0
    created_at: float = field(default_factory=time.monotonic)


class BrowserPool(BrowserClientPort):
    """Pool of Playwright browser contexts with fingerprint rotation."""

    def __init__(
        self,
        fingerprint_strategy: FingerprintStrategy | None = None,
        config: BrowserPoolConfig | None = None,
    ) -> None:
        self._fingerprint = fingerprint_strategy or BrowserFingerprintStrategy()
        self._config = config or BrowserPoolConfig()
        self._browser: object | None = None
        self._playwright: object | None = None
        self._contexts: list[_BrowserContext] = []
        self._lock = asyncio.Lock()
        self._initialized = False

    async def _ensure_browser(self) -> None:
        if self._initialized:
            return
        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(  # type: ignore[union-attr]
                headless=self._config.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-extensions",
                ],
            )
            self._initialized = True
            logger.info("Browser pool initialized (size=%d)", self._config.pool_size)
        except ImportError:
            raise RuntimeError("playwright not installed: pip install playwright && playwright install chromium")

    async def _acquire_context(self) -> _BrowserContext:
        """Get or create a browser context with fresh fingerprint."""
        async with self._lock:
            await self._ensure_browser()

            # Clean expired contexts
            now = time.monotonic()
            active: list[_BrowserContext] = []
            for ctx in self._contexts:
                expired = (now - ctx.created_at) > self._config.context_ttl
                exhausted = ctx.pages_used >= self._config.max_pages_per_context
                if expired or exhausted:
                    try:
                        await ctx.context.close()  # type: ignore[union-attr]
                    except Exception:
                        pass
                else:
                    active.append(ctx)
            self._contexts = active

            # Reuse or create
            if self._contexts and len(self._contexts) < self._config.pool_size:
                # Return least-used context
                ctx = min(self._contexts, key=lambda c: c.pages_used)
                ctx.pages_used += 1
                return ctx

            if len(self._contexts) >= self._config.pool_size:
                ctx = min(self._contexts, key=lambda c: c.pages_used)
                ctx.pages_used += 1
                return ctx

            # Create new context with fresh fingerprint
            fp = self._fingerprint.generate()
            context = await self._browser.new_context(  # type: ignore[union-attr]
                user_agent=fp.get("user_agent", ""),
                viewport=fp.get("viewport", {"width": 1920, "height": 1080}),
                locale=fp.get("locale", "en-US"),
                timezone_id=fp.get("timezone_id", "America/New_York"),
                color_scheme=fp.get("color_scheme", "light"),
                device_scale_factor=fp.get("device_scale_factor", 1),
                has_touch=fp.get("has_touch", False),
                is_mobile=fp.get("is_mobile", False),
                extra_http_headers=fp.get("extra_headers", {}),
            )

            # Anti-detection script
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['es', 'en-US', 'en'] });
                window.chrome = { runtime: {} };
            """)

            bc = _BrowserContext(context=context, pages_used=1)
            self._contexts.append(bc)
            return bc

    async def fetch_rendered(
        self,
        url: str,
        *,
        marketplace: Marketplace,
        wait_selector: str | None = None,
        scroll: bool = False,
        timeout: float = 45.0,
    ) -> tuple[str, str]:
        """Fetch page with browser rendering."""
        ctx = await self._acquire_context()
        page = await ctx.context.new_page()  # type: ignore[union-attr]

        try:
            page.set_default_timeout(timeout * 1000)
            response = await page.goto(url, wait_until="domcontentloaded")

            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=10000)
                except Exception:
                    pass

            if scroll:
                await self._smooth_scroll(page)

            html = await page.content()
            final_url = page.url
            return html, final_url

        finally:
            await page.close()

    @staticmethod
    async def _smooth_scroll(page: object) -> None:
        """Simulate human scrolling behavior."""
        import random
        for _ in range(random.randint(2, 5)):
            await page.evaluate(  # type: ignore[union-attr]
                "window.scrollBy(0, window.innerHeight * (0.5 + Math.random() * 0.5))"
            )
            await asyncio.sleep(0.3 + 0.5 * random.random())

    async def close(self) -> None:
        for ctx in self._contexts:
            try:
                await ctx.context.close()  # type: ignore[union-attr]
            except Exception:
                pass
        self._contexts.clear()
        if self._browser:
            try:
                await self._browser.close()  # type: ignore[union-attr]
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()  # type: ignore[union-attr]
            except Exception:
                pass
        self._initialized = False
