"""Anti-blocking strategies — header rotation, delay jitter, UA pools.

These strategies are injected into HTTP and browser clients to avoid detection.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass


# ── User-Agent Pool ────────────────────────────────────────────

_USER_AGENTS: list[str] = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Chrome on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Firefox on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Safari on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

_ACCEPT_LANGUAGES: list[str] = [
    "es-AR,es;q=0.9,en;q=0.8",
    "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
    "es-CL,es;q=0.9,en;q=0.8",
    "es-CO,es;q=0.9,en;q=0.8",
    "en-US,en;q=0.9,es;q=0.8",
    "en-US,en;q=0.9",
    "es,en;q=0.9",
]

_VIEWPORTS: list[tuple[int, int]] = [
    (1920, 1080),
    (1366, 768),
    (1440, 900),
    (1536, 864),
    (1280, 720),
    (2560, 1440),
    (1680, 1050),
]


# ── Strategy Interfaces ───────────────────────────────────────


class HeaderStrategy(ABC):
    @abstractmethod
    def generate(self) -> dict[str, str]: ...


class DelayStrategy(ABC):
    @abstractmethod
    def compute(self) -> float:
        """Return delay in seconds."""


class FingerprintStrategy(ABC):
    @abstractmethod
    def generate(self) -> dict[str, object]:
        """Return browser fingerprint config."""


# ── Concrete Strategies ───────────────────────────────────────


class RandomHeaderStrategy(HeaderStrategy):
    """Generates randomized but realistic browser headers."""

    def generate(self) -> dict[str, str]:
        ua = random.choice(_USER_AGENTS)
        lang = random.choice(_ACCEPT_LANGUAGES)
        headers: dict[str, str] = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": lang,
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }
        # Randomly include optional headers
        if random.random() > 0.3:
            headers["Sec-Ch-Ua-Platform"] = random.choice([
                '"Windows"', '"macOS"', '"Linux"'
            ])
        if random.random() > 0.5:
            headers["Sec-Ch-Ua-Mobile"] = "?0"
        return headers


@dataclass(frozen=True, slots=True)
class JitterDelayConfig:
    base: float = 2.0
    factor: float = 0.5
    min_delay: float = 0.5
    max_delay: float = 10.0


class JitterDelayStrategy(DelayStrategy):
    """Randomized delay with configurable jitter."""

    def __init__(self, config: JitterDelayConfig | None = None) -> None:
        self._config = config or JitterDelayConfig()

    def compute(self) -> float:
        c = self._config
        jitter = c.base * c.factor * (2 * random.random() - 1)
        delay = c.base + jitter
        return max(c.min_delay, min(c.max_delay, delay))


class BrowserFingerprintStrategy(FingerprintStrategy):
    """Generates randomized browser fingerprints for Playwright."""

    def generate(self) -> dict[str, object]:
        ua = random.choice(_USER_AGENTS)
        width, height = random.choice(_VIEWPORTS)
        lang = random.choice(_ACCEPT_LANGUAGES).split(",")[0]

        return {
            "user_agent": ua,
            "viewport": {"width": width, "height": height},
            "locale": lang.split("-")[0] if "-" in lang else lang,
            "timezone_id": random.choice([
                "America/Argentina/Buenos_Aires",
                "America/Mexico_City",
                "America/Santiago",
                "America/Bogota",
                "America/New_York",
                "America/Los_Angeles",
            ]),
            "color_scheme": random.choice(["light", "dark", "no-preference"]),
            "device_scale_factor": random.choice([1, 1.25, 1.5, 2]),
            "has_touch": False,
            "is_mobile": False,
            "extra_headers": {
                "Accept-Language": random.choice(_ACCEPT_LANGUAGES),
            },
        }
