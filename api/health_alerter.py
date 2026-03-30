"""
Health Alerter — background task that monitors DB, Redis, and scraper freshness.

Sends alerts via existing Telegram/Webhook channels when failures exceed threshold.
"""
import asyncio
import logging
import os

import redis.asyncio as aioredis
from sqlalchemy import text

from .database import async_session

logger = logging.getLogger(__name__)

HEALTH_ALERT_INTERVAL = int(os.getenv("HEALTH_ALERT_INTERVAL", "60"))
HEALTH_ALERT_THRESHOLD = int(os.getenv("HEALTH_ALERT_THRESHOLD", "3"))
HEALTH_TELEGRAM_CHAT_ID = os.getenv("HEALTH_TELEGRAM_CHAT_ID", "")


class HealthAlerter:
    """Monitors system health and sends alerts on consecutive failures."""

    def __init__(self):
        self._consecutive_failures = 0
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        logger.info("Health alerter started (interval=%ds, threshold=%d)",
                     HEALTH_ALERT_INTERVAL, HEALTH_ALERT_THRESHOLD)
        while self._running:
            try:
                issues = await self._check_all()
                if issues:
                    self._consecutive_failures += 1
                    logger.warning("Health check failed (%d/%d): %s",
                                   self._consecutive_failures, HEALTH_ALERT_THRESHOLD,
                                   "; ".join(issues))
                    if self._consecutive_failures >= HEALTH_ALERT_THRESHOLD:
                        await self._send_alert(issues)
                        self._consecutive_failures = 0
                else:
                    if self._consecutive_failures > 0:
                        logger.info("Health check recovered after %d failures",
                                    self._consecutive_failures)
                    self._consecutive_failures = 0
            except Exception:
                logger.error("Health alerter error", exc_info=True)
            await asyncio.sleep(HEALTH_ALERT_INTERVAL)

    async def stop(self) -> None:
        self._running = False

    async def _check_all(self) -> list[str]:
        issues: list[str] = []

        # Check DB connectivity
        try:
            async with async_session() as db:
                await db.execute(text("SELECT 1"))
        except Exception as e:
            issues.append(f"DB unreachable: {e}")

        # Check Redis
        try:
            r = aioredis.from_url(
                os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                decode_responses=True,
            )
            try:
                await r.ping()
            finally:
                await r.aclose()
        except Exception as e:
            issues.append(f"Redis unreachable: {e}")

        # Check scraper freshness (last listing < 1h)
        try:
            async with async_session() as db:
                row = await db.execute(text(
                    "SELECT EXTRACT(EPOCH FROM (now() - max(scraped_at)))/60 "
                    "FROM product_listings"
                ))
                minutes = row.scalar()
                if minutes is not None and minutes > 60:
                    issues.append(f"No new listings for {minutes:.0f} min")
        except Exception:
            pass  # DB issue already captured above

        return issues

    async def _send_alert(self, issues: list[str]) -> None:
        message = f"⚠️ CompraVenta Health Alert:\n" + "\n".join(f"• {i}" for i in issues)
        logger.error("HEALTH ALERT: %s", "; ".join(issues))

        # Try Telegram
        if HEALTH_TELEGRAM_CHAT_ID:
            try:
                import httpx
                bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
                if bot_token:
                    async with httpx.AsyncClient(timeout=10) as client:
                        await client.post(
                            f"https://api.telegram.org/bot{bot_token}/sendMessage",
                            json={
                                "chat_id": HEALTH_TELEGRAM_CHAT_ID,
                                "text": message,
                                "parse_mode": "Markdown",
                            },
                        )
                    logger.info("Health alert sent to Telegram chat %s", HEALTH_TELEGRAM_CHAT_ID)
            except Exception:
                logger.error("Failed to send Telegram health alert", exc_info=True)
