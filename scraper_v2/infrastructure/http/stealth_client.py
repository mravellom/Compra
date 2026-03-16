"""Stealth HTTP Client — httpx-based implementation of HttpClientPort.

Features:
- Automatic header rotation per request
- Proxy injection
- Connection pooling with per-domain limits
- Block detection integration
"""

from __future__ import annotations

import logging

import httpx

from scraper_v2.domain.enums import Marketplace
from scraper_v2.domain.models import ProxyEndpoint
from scraper_v2.domain.ports import HttpClientPort
from scraper_v2.application.strategies.anti_blocking import HeaderStrategy, RandomHeaderStrategy

logger = logging.getLogger(__name__)


class StealthHttpClient(HttpClientPort):
    """Production HTTP client with stealth capabilities."""

    def __init__(
        self,
        header_strategy: HeaderStrategy | None = None,
        max_connections: int = 50,
        max_keepalive: int = 20,
        default_timeout: float = 30.0,
    ) -> None:
        self._headers = header_strategy or RandomHeaderStrategy()
        self._timeout = default_timeout
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._max_connections = max_connections
        self._max_keepalive = max_keepalive

    def _get_client(self, proxy_url: str | None = None) -> httpx.AsyncClient:
        key = proxy_url or "__direct__"
        if key not in self._clients:
            transport = httpx.AsyncHTTPTransport(
                retries=0,  # We handle retries at application level
                limits=httpx.Limits(
                    max_connections=self._max_connections,
                    max_keepalive_connections=self._max_keepalive,
                ),
                proxy=proxy_url,
            )
            self._clients[key] = httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=True,
                http2=True,
            )
        return self._clients[key]

    async def fetch(
        self,
        url: str,
        *,
        marketplace: Marketplace,
        proxy: ProxyEndpoint | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> tuple[int, str, dict[str, str]]:
        """Fetch URL with stealth headers and optional proxy."""
        request_headers = self._headers.generate()
        if headers:
            request_headers.update(headers)

        # Add marketplace-specific referer
        request_headers["Referer"] = f"https://{marketplace.domain}/"

        proxy_url = proxy.url if proxy else None
        client = self._get_client(proxy_url)

        response = await client.get(
            url,
            headers=request_headers,
            timeout=timeout,
        )

        resp_headers = dict(response.headers)
        return response.status_code, response.text, resp_headers

    async def close(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
