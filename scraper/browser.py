"""
Shared Playwright browser manager for JS-rendered sites.

Provides a singleton-like async browser pool that scrapers can share,
avoiding the overhead of launching multiple browser instances.
"""
import asyncio
import logging
import random

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

# Reuse desktop UAs from stealth module
_BROWSER_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 720},
]


class PlaywrightBrowser:
    """
    Manages a shared Chromium browser instance.

    Usage:
        browser_mgr = PlaywrightBrowser()
        async with browser_mgr:
            page = await browser_mgr.new_page(locale="es-CL")
            await page.goto(url)
            content = await page.content()
            await page.close()
    """

    def __init__(self, headless: bool = True):
        self._headless = headless
        self._playwright = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def _ensure_browser(self) -> Browser:
        if self._browser and self._browser.is_connected():
            return self._browser

        async with self._lock:
            # Double-check after acquiring lock
            if self._browser and self._browser.is_connected():
                return self._browser

            logger.info("Launching Playwright Chromium (headless=%s)", self._headless)
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
            return self._browser

    async def new_page(
        self,
        locale: str = "en-US",
        timezone_id: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Page:
        """Create a new page with randomized fingerprint."""
        browser = await self._ensure_browser()

        ua = random.choice(_BROWSER_USER_AGENTS)
        viewport = random.choice(_VIEWPORTS)

        context: BrowserContext = await browser.new_context(
            user_agent=ua,
            viewport=viewport,
            locale=locale,
            timezone_id=timezone_id or "America/New_York",
            extra_http_headers=extra_headers or {},
            java_script_enabled=True,
        )

        # Mask webdriver detection
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)

        page = await context.new_page()
        return page

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def __aenter__(self):
        await self._ensure_browser()
        return self

    async def __aexit__(self, *args):
        await self.close()


# Module-level singleton for sharing across scrapers
_global_browser: PlaywrightBrowser | None = None
_global_lock = asyncio.Lock()


async def get_shared_browser() -> PlaywrightBrowser:
    """Get or create the shared browser instance."""
    global _global_browser
    async with _global_lock:
        if _global_browser is None:
            _global_browser = PlaywrightBrowser()
        return _global_browser


async def close_shared_browser() -> None:
    """Shutdown the shared browser (call on app exit)."""
    global _global_browser
    async with _global_lock:
        if _global_browser:
            await _global_browser.close()
            _global_browser = None
