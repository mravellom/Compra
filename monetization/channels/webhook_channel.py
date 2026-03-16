"""
Webhook Channel Adapter — sends opportunity data to external HTTP endpoints.

Use cases:
  - Zapier/Make/n8n automation triggers
  - Paid API subscribers
  - Partner integrations
  - Custom dashboards
"""
import asyncio
import logging
import os
from dataclasses import asdict

import httpx

from ..publisher import ChannelAdapter, ChannelFilter, OpportunityEvent, DeliveryResult

logger = logging.getLogger(__name__)


class WebhookChannel(ChannelAdapter):
    """
    POST opportunity events as JSON to configured webhook URLs.

    Usage:
        channel = WebhookChannel(
            webhook_urls=["https://hooks.zapier.com/xxx"],
            filter_rules=ChannelFilter(min_opportunity_score=60),
            headers={"Authorization": "Bearer xxx"},
        )
        publisher.register_channel(channel)
    """

    def __init__(
        self,
        webhook_urls: list[str] | None = None,
        headers: dict[str, str] | None = None,
        filter_rules: ChannelFilter | None = None,
        timeout: float = 15.0,
    ):
        super().__init__("webhook", filter_rules)
        self.webhook_urls = webhook_urls or []
        self.headers = headers or {"Content-Type": "application/json"}
        self.timeout = timeout

    def _format_payload(self, event: OpportunityEvent) -> dict:
        return {
            "event_type": event.event_type,
            "opportunity": {
                "id": event.opportunity_id,
                "product_name": event.product_name,
                "buy_marketplace": event.buy_marketplace,
                "sell_marketplace": event.sell_marketplace,
                "route": event.route,
                "buy_price": event.buy_price,
                "sell_price": event.sell_price,
                "net_profit": event.net_profit,
                "roi": event.roi,
                "opportunity_score": event.opportunity_score,
                "risk_score": event.risk_score,
                "confidence_level": event.confidence_level,
                "risk_class": event.risk_class,
                "buy_url": event.buy_url,
                "sell_url": event.sell_url,
                "image_url": event.image_url,
                "category": event.category,
            },
            "timestamp": event.timestamp,
        }

    async def deliver(self, event: OpportunityEvent) -> DeliveryResult:
        if not self.webhook_urls:
            return DeliveryResult(self.name, False, "No webhook URLs configured")

        payload = self._format_payload(event)

        async def _post_one(client: httpx.AsyncClient, url: str):
            try:
                resp = await client.post(url, json=payload, headers=self.headers)
                if resp.status_code >= 400:
                    return f"{url}: HTTP {resp.status_code}"
            except Exception as e:
                return f"{url}: {e}"
            return None

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            results = await asyncio.gather(*[_post_one(client, url) for url in self.webhook_urls])
            errors = [r for r in results if r is not None]

        if errors:
            return DeliveryResult(self.name, False, "; ".join(errors))
        return DeliveryResult(self.name, True)
