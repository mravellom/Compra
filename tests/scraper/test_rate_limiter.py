"""
Unit Tests — Rate Limiter & Retry Logic.

Tests:
  - Token bucket replenishment
  - Per-domain isolation
  - Burst capacity
  - fetch_with_retry retry behavior
  - 429 handling
  - Exponential backoff
"""
import asyncio
import time

import httpx
import pytest

from scraper.rate_limiter import RateLimiter, fetch_with_retry


class TestRateLimiter:

    @pytest.mark.asyncio
    async def test_burst_allows_multiple_immediate(self):
        """Burst of 3 allows 3 immediate requests without waiting."""
        rl = RateLimiter(requests_per_second=1.0, burst=3)
        t0 = time.monotonic()
        for _ in range(3):
            await rl.acquire("test")
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5  # All 3 within burst, near-instant

    @pytest.mark.asyncio
    async def test_exceeding_burst_causes_delay(self):
        """4th request beyond burst=3 should incur a wait."""
        rl = RateLimiter(requests_per_second=10.0, burst=3)
        for _ in range(3):
            await rl.acquire("test")
        t0 = time.monotonic()
        await rl.acquire("test")
        elapsed = time.monotonic() - t0
        # At 10 req/s, wait should be ~0.1s
        assert elapsed >= 0.05

    @pytest.mark.asyncio
    async def test_per_domain_isolation(self):
        """Different domains maintain separate token buckets."""
        rl = RateLimiter(requests_per_second=1.0, burst=2)
        # Exhaust domain A
        await rl.acquire("domain_a")
        await rl.acquire("domain_a")
        # Domain B should still have full burst
        t0 = time.monotonic()
        await rl.acquire("domain_b")
        elapsed = time.monotonic() - t0
        assert elapsed < 0.1  # domain_b unaffected

    @pytest.mark.asyncio
    async def test_tokens_replenish_over_time(self):
        """Tokens replenish at the configured rate."""
        rl = RateLimiter(requests_per_second=100.0, burst=1)
        await rl.acquire("test")  # Use the 1 token
        await asyncio.sleep(0.05)  # 100 req/s → 5 tokens in 0.05s
        t0 = time.monotonic()
        await rl.acquire("test")
        elapsed = time.monotonic() - t0
        assert elapsed < 0.05  # Should have replenished


class TestFetchWithRetry:

    @pytest.mark.asyncio
    async def test_success_on_first_try(self, mocker):
        """Successful response on first attempt."""
        mock_resp = mocker.MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.raise_for_status = mocker.MagicMock()

        client = mocker.AsyncMock(spec=httpx.AsyncClient)
        client.get.return_value = mock_resp

        result = await fetch_with_retry(client, "https://example.com", max_retries=3)
        assert result.status_code == 200
        client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_on_connect_error(self, mocker):
        """Retries on ConnectError then succeeds."""
        mock_resp = mocker.MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.raise_for_status = mocker.MagicMock()

        client = mocker.AsyncMock(spec=httpx.AsyncClient)
        client.get.side_effect = [
            httpx.ConnectError("Connection refused"),
            mock_resp,
        ]

        # Patch asyncio.sleep to skip waits
        mocker.patch("scraper.rate_limiter.asyncio.sleep", new_callable=mocker.AsyncMock)

        result = await fetch_with_retry(client, "https://example.com", max_retries=3, base_delay=0.01)
        assert result.status_code == 200
        assert client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_429_retries_with_backoff(self, mocker):
        """429 response triggers retry with Retry-After header."""
        mock_429 = mocker.MagicMock(spec=httpx.Response)
        mock_429.status_code = 429
        mock_429.headers = {"Retry-After": "0.01"}

        mock_200 = mocker.MagicMock(spec=httpx.Response)
        mock_200.status_code = 200
        mock_200.raise_for_status = mocker.MagicMock()

        client = mocker.AsyncMock(spec=httpx.AsyncClient)
        client.get.side_effect = [mock_429, mock_200]

        mocker.patch("scraper.rate_limiter.asyncio.sleep", new_callable=mocker.AsyncMock)

        result = await fetch_with_retry(client, "https://example.com", max_retries=3)
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_max_retries_exhausted(self, mocker):
        """Raises after max_retries exhausted."""
        client = mocker.AsyncMock(spec=httpx.AsyncClient)
        client.get.side_effect = httpx.ConnectError("down")

        mocker.patch("scraper.rate_limiter.asyncio.sleep", new_callable=mocker.AsyncMock)

        with pytest.raises(httpx.ConnectError):
            await fetch_with_retry(
                client, "https://example.com", max_retries=2, base_delay=0.01
            )
        assert client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_rate_limiter_integration(self, mocker):
        """Rate limiter is called before the request."""
        rl = RateLimiter(requests_per_second=100.0, burst=10)
        spy = mocker.patch.object(rl, "acquire", new_callable=mocker.AsyncMock)

        mock_resp = mocker.MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.raise_for_status = mocker.MagicMock()

        client = mocker.AsyncMock(spec=httpx.AsyncClient)
        client.get.return_value = mock_resp

        await fetch_with_retry(
            client, "https://example.com",
            rate_limiter=rl, domain="test.com",
        )
        spy.assert_called_once_with("test.com")
