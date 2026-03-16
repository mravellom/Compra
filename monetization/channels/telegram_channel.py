"""
Telegram Channel Adapter — sends formatted opportunity alerts to Telegram.

Supports:
  - Individual chat alerts
  - Channel broadcasts (public/private)
  - Formatted markdown messages with product details
  - Rate limiting to avoid Telegram API throttling
"""
import asyncio
import logging
import os

import httpx

from ..publisher import ChannelAdapter, ChannelFilter, OpportunityEvent, DeliveryResult

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


class TelegramChannel(ChannelAdapter):
    """
    Sends opportunity alerts to one or more Telegram chats.

    Usage:
        channel = TelegramChannel(
            chat_ids=["123456789", "@my_channel"],
            filter_rules=ChannelFilter(min_opportunity_score=50, min_roi=0.20),
        )
        publisher.register_channel(channel)
    """

    def __init__(
        self,
        chat_ids: list[str] | None = None,
        bot_token: str | None = None,
        filter_rules: ChannelFilter | None = None,
    ):
        super().__init__("telegram", filter_rules)
        self.chat_ids = chat_ids or []
        self.bot_token = bot_token or TELEGRAM_BOT_TOKEN
        self._semaphore = asyncio.Semaphore(5)  # Max 5 concurrent sends

    def _format_message(self, event: OpportunityEvent) -> str:
        """Format opportunity as Telegram markdown message."""
        # Emoji based on ROI
        if event.roi >= 0.5:
            emoji = "🔥"
        elif event.roi >= 0.3:
            emoji = "🟢"
        elif event.roi >= 0.15:
            emoji = "📈"
        else:
            emoji = "📊"

        # Risk class badge
        risk_badges = {
            "prime": "🏆 PRIME",
            "standard": "✅ STANDARD",
            "speculative": "⚠️ SPECULATIVE",
            "avoid": "🚫 AVOID",
        }
        risk_badge = risk_badges.get(event.risk_class, event.risk_class.upper())

        msg = (
            f"{emoji} *Nueva Oportunidad de Arbitraje*\n\n"
            f"📦 *{event.product_name[:80]}*\n"
            f"🏷 {risk_badge} | Score: {event.opportunity_score:.0f}/100\n\n"
            f"💰 Compra: ${event.buy_price:.2f} ({event.buy_marketplace})\n"
            f"💵 Venta: ${event.sell_price:.2f} ({event.sell_marketplace})\n"
            f"📈 Profit: *${event.net_profit:.2f}* (ROI {event.roi * 100:.1f}%)\n"
            f"🛣 Ruta: {event.route}\n"
        )

        if event.buy_url:
            msg += f"\n[Comprar]({event.buy_url})"
        if event.sell_url:
            msg += f" | [Ver venta]({event.sell_url})"

        return msg

    async def deliver(self, event: OpportunityEvent) -> DeliveryResult:
        if not self.bot_token or not self.chat_ids:
            return DeliveryResult(self.name, False, "No bot token or chat IDs configured")

        message = self._format_message(event)
        errors = []

        async def _send_one(client: httpx.AsyncClient, chat_id: str):
            async with self._semaphore:
                try:
                    resp = await client.post(
                        TELEGRAM_API.format(token=self.bot_token),
                        json={
                            "chat_id": chat_id,
                            "text": message,
                            "parse_mode": "Markdown",
                            "disable_web_page_preview": True,
                        },
                    )
                    if resp.status_code != 200:
                        return f"chat {chat_id}: HTTP {resp.status_code}"
                except Exception as e:
                    return f"chat {chat_id}: {e}"
                return None

        async with httpx.AsyncClient(timeout=10) as client:
            results = await asyncio.gather(*[_send_one(client, cid) for cid in self.chat_ids])
            errors = [r for r in results if r is not None]

        if errors:
            return DeliveryResult(self.name, False, "; ".join(errors))
        return DeliveryResult(self.name, True)
