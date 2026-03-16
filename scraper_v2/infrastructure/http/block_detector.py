"""Block Detector — identifies anti-bot blocks from HTTP responses."""

from __future__ import annotations

import re

from scraper_v2.domain.enums import BlockType
from scraper_v2.domain.models import BlockDetection
from scraper_v2.domain.ports import BlockDetectorPort

# Patterns that indicate CAPTCHA pages
_CAPTCHA_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"recaptcha", re.IGNORECASE),
    re.compile(r"hcaptcha", re.IGNORECASE),
    re.compile(r"captcha[_-]?challenge", re.IGNORECASE),
    re.compile(r"g-recaptcha", re.IGNORECASE),
    re.compile(r"cf-challenge", re.IGNORECASE),
    re.compile(r"px-captcha", re.IGNORECASE),
]

# Patterns that indicate WAF/bot detection
_WAF_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"access\s+denied", re.IGNORECASE),
    re.compile(r"bot\s+detected", re.IGNORECASE),
    re.compile(r"automated\s+access", re.IGNORECASE),
    re.compile(r"suspicious\s+activity", re.IGNORECASE),
    re.compile(r"robot\s+check", re.IGNORECASE),
    re.compile(r"pardon\s+our\s+interruption", re.IGNORECASE),  # Amazon
    re.compile(r"no\s+pudimos\s+confirmar", re.IGNORECASE),  # MercadoLibre
]


class HttpBlockDetector(BlockDetectorPort):
    """Detects blocks from HTTP status codes and response body patterns."""

    def detect(
        self, status_code: int, body: str, headers: dict[str, str]
    ) -> BlockDetection:
        # Status code checks
        if status_code == 429:
            retry_after = float(headers.get("retry-after", "60"))
            return BlockDetection(
                blocked=True,
                block_type=BlockType.RATE_LIMIT,
                message="HTTP 429 Too Many Requests",
                retry_after=retry_after,
            )

        if status_code == 403:
            return BlockDetection(
                blocked=True,
                block_type=BlockType.IP_BAN,
                message="HTTP 403 Forbidden",
            )

        if status_code == 503:
            # Could be WAF or legitimate maintenance
            if len(body) < 2000:
                return BlockDetection(
                    blocked=True,
                    block_type=BlockType.WAF,
                    message="HTTP 503 with small body (likely WAF)",
                )

        if status_code >= 400:
            return BlockDetection(blocked=False)

        # Content analysis for 200 responses that are actually blocks
        if len(body) < 1000 and status_code == 200:
            # Suspiciously small page
            for pattern in _CAPTCHA_PATTERNS:
                if pattern.search(body):
                    return BlockDetection(
                        blocked=True,
                        block_type=BlockType.CAPTCHA,
                        message=f"CAPTCHA detected: {pattern.pattern}",
                    )

        # Full body analysis
        for pattern in _CAPTCHA_PATTERNS:
            if pattern.search(body[:5000]):
                return BlockDetection(
                    blocked=True,
                    block_type=BlockType.CAPTCHA,
                    message=f"CAPTCHA in page: {pattern.pattern}",
                )

        for pattern in _WAF_PATTERNS:
            if pattern.search(body[:5000]):
                return BlockDetection(
                    blocked=True,
                    block_type=BlockType.WAF,
                    message=f"WAF block: {pattern.pattern}",
                )

        return BlockDetection(blocked=False)
