"""
RSS Feed Channel Adapter — generates an RSS/Atom feed of opportunities.

Enables:
  - Public opportunity feed
  - Email digest integration (via RSS-to-email services)
  - Browser RSS readers
  - Podcast apps (some support RSS)
"""
import html
import logging
import time
from dataclasses import dataclass, field

from ..publisher import ChannelAdapter, ChannelFilter, OpportunityEvent, DeliveryResult

logger = logging.getLogger(__name__)


class RSSChannel(ChannelAdapter):
    """
    Accumulates opportunities into an RSS feed that can be served via API.

    Usage:
        rss = RSSChannel(
            title="CompraVenta Opportunities",
            max_items=100,
            filter_rules=ChannelFilter(min_opportunity_score=50),
        )
        publisher.register_channel(rss)

        # Later, serve via API endpoint:
        @app.get("/feed/rss")
        async def rss_feed():
            return Response(rss.generate_xml(), media_type="application/rss+xml")
    """

    def __init__(
        self,
        title: str = "CompraVenta — Oportunidades de Arbitraje",
        description: str = "Feed automatico de oportunidades de arbitraje cross-marketplace",
        link: str = "http://localhost:4200",
        max_items: int = 100,
        filter_rules: ChannelFilter | None = None,
    ):
        super().__init__("rss", filter_rules)
        self.title = title
        self.description = description
        self.link = link
        self.max_items = max_items
        self._items: list[OpportunityEvent] = []

    async def deliver(self, event: OpportunityEvent) -> DeliveryResult:
        """Store event for RSS generation."""
        self._items.append(event)
        if len(self._items) > self.max_items:
            self._items = self._items[-self.max_items:]
        return DeliveryResult(self.name, True)

    def generate_xml(self) -> str:
        """Generate RSS 2.0 XML from stored events."""
        items_xml = ""
        for event in reversed(self._items):
            title = html.escape(f"${event.net_profit:.0f} profit — {event.product_name[:60]}")
            desc = html.escape(
                f"Compra en {event.buy_marketplace} a ${event.buy_price:.2f}, "
                f"vende en {event.sell_marketplace} a ${event.sell_price:.2f}. "
                f"ROI {event.roi * 100:.1f}%. Score {event.opportunity_score:.0f}/100. "
                f"Risk: {event.risk_class}."
            )
            pub_date = time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(event.timestamp))
            link = event.buy_url or self.link

            items_xml += f"""
        <item>
            <title>{title}</title>
            <description>{desc}</description>
            <link>{html.escape(link)}</link>
            <guid>opp-{event.opportunity_id}-{int(event.timestamp)}</guid>
            <pubDate>{pub_date}</pubDate>
            <category>{html.escape(event.route)}</category>
        </item>"""

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
    <channel>
        <title>{html.escape(self.title)}</title>
        <description>{html.escape(self.description)}</description>
        <link>{html.escape(self.link)}</link>
        <lastBuildDate>{time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime())}</lastBuildDate>
        <ttl>15</ttl>{items_xml}
    </channel>
</rss>"""

    @property
    def item_count(self) -> int:
        return len(self._items)
