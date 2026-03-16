"""
Unit Tests — Stealth & Anti-Blocking.

Tests:
  - Random headers generation
  - Block detection (captcha, 429, 403, 503)
  - Proxy pool rotation and health tracking
"""
import pytest
from unittest.mock import MagicMock

from scraper.stealth import (
    random_headers,
    detect_block,
    ProxyPool,
    ProxyEntry,
)


class TestRandomHeaders:

    def test_returns_dict(self):
        h = random_headers()
        assert isinstance(h, dict)

    def test_contains_user_agent(self):
        h = random_headers()
        assert "User-Agent" in h
        assert len(h["User-Agent"]) > 20

    def test_contains_accept_language(self):
        h = random_headers()
        assert "Accept-Language" in h

    def test_custom_accept_language(self):
        h = random_headers(accept_language="fr-FR")
        assert h["Accept-Language"] == "fr-FR"

    def test_multiple_calls_vary(self):
        """Headers should vary across calls (randomized UA)."""
        headers_set = set()
        for _ in range(20):
            h = random_headers()
            headers_set.add(h["User-Agent"])
        # With 49 UAs, 20 calls should produce at least 2 different
        assert len(headers_set) >= 2


class TestBlockDetection:

    def _make_response(self, status_code=200, text="<html>Normal page</html>"):
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = text
        resp.headers = {}
        return resp

    def test_normal_page_not_blocked(self):
        resp = self._make_response(200, "<html><body>Products here</body></html>" * 50)
        result = detect_block(resp)
        assert result.is_blocked is False

    def test_429_detected_as_rate_limit(self):
        resp = self._make_response(429, "Too many requests")
        result = detect_block(resp)
        assert result.is_blocked is True
        assert result.block_type == "rate_limit"

    def test_403_detected_as_access_denied(self):
        resp = self._make_response(403, "Access Denied")
        result = detect_block(resp)
        assert result.is_blocked is True
        assert result.block_type == "access_denied"

    def test_captcha_in_body(self):
        resp = self._make_response(200, "<html><form>captcha verification required</form></html>")
        result = detect_block(resp)
        assert result.is_blocked is True
        assert result.block_type in ("captcha", "bot_check")

    def test_recaptcha_detected(self):
        resp = self._make_response(200, '<div class="g-recaptcha">solve me</div>' + "x" * 500)
        result = detect_block(resp)
        assert result.is_blocked is True

    def test_amazon_bot_check(self):
        resp = self._make_response(200, "to discuss automated access to Amazon" + "x" * 500)
        result = detect_block(resp)
        assert result.is_blocked is True

    def test_503_with_captcha(self):
        resp = self._make_response(503, "captcha challenge required" + "x" * 500)
        result = detect_block(resp)
        assert result.is_blocked is True
        assert result.block_type == "bot_check"

    def test_small_response_suspicious(self):
        resp = self._make_response(200, "tiny")
        result = detect_block(resp)
        assert result.is_blocked is True


class TestProxyPool:

    @pytest.mark.asyncio
    async def test_no_proxies_returns_none(self):
        pool = ProxyPool(proxy_urls=[])
        result = await pool.get_proxy()
        assert result is None

    @pytest.mark.asyncio
    async def test_round_robin(self):
        pool = ProxyPool(proxy_urls=["http://p1:8080", "http://p2:8080"])
        p1 = await pool.get_proxy()
        p2 = await pool.get_proxy()
        assert p1 != p2

    @pytest.mark.asyncio
    async def test_report_success_decrements_failures(self):
        pool = ProxyPool(proxy_urls=["http://p1:8080"])
        proxy = "http://p1:8080"
        # Simulate some failures
        await pool.report_failure(proxy)
        await pool.report_failure(proxy)
        entry = pool._proxies[0]
        assert entry.fail_count == 2

        await pool.report_success(proxy)
        assert entry.fail_count == 1

    @pytest.mark.asyncio
    async def test_cooldown_after_max_fails(self):
        pool = ProxyPool(proxy_urls=["http://p1:8080"])
        proxy = "http://p1:8080"
        for _ in range(3):  # MAX_FAILS = 3
            await pool.report_failure(proxy)
        entry = pool._proxies[0]
        assert entry.cooldown_until > 0
