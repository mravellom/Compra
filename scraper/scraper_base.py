import random
from abc import ABC, abstractmethod

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright_stealth import Stealth

from .schemas import RawListing

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


class BaseScraper(ABC):
    """Clase base para todos los scrapers de marketplaces."""

    def __init__(self, proxy: dict | None = None):
        """
        Args:
            proxy: Dict con formato Playwright: {"server": "http://host:port", "username": "...", "password": "..."}
        """
        self.proxy = proxy
        self._playwright = None
        self._browser: Browser | None = None

    @property
    @abstractmethod
    def marketplace_id(self) -> str:
        """Identificador unico del marketplace (ej: 'ebay', 'mercadolibre')."""
        ...

    def _random_user_agent(self) -> str:
        return random.choice(USER_AGENTS)

    async def _create_context(self) -> BrowserContext:
        context = await self._browser.new_context(
            user_agent=self._random_user_agent(),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        return context

    async def start(self) -> None:
        stealth = Stealth()
        self._stealth_cm = stealth.use_async(async_playwright())
        self._playwright = await self._stealth_cm.__aenter__()
        launch_opts: dict = {"headless": True}
        if self.proxy:
            launch_opts["proxy"] = self.proxy
        self._browser = await self._playwright.chromium.launch(**launch_opts)

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if hasattr(self, '_stealth_cm') and self._stealth_cm:
            await self._stealth_cm.__aexit__(None, None, None)

    async def new_page(self) -> Page:
        context = await self._browser.new_context(
            user_agent=self._random_user_agent(),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        page = await context.new_page()
        return page

    @abstractmethod
    async def scrape(self, search_term: str, max_results: int = 20) -> list[RawListing]:
        """Ejecuta el scraping y devuelve una lista de RawListing."""
        ...
