"""
Unit Tests — Channel Adapters (Telegram, Webhook, RSS).

Tests:
  - Telegram message formatting
  - Webhook JSON payload structure
  - RSS XML generation
  - Error handling for missing configuration
"""
import pytest

from monetization.publisher import ChannelFilter, OpportunityEvent, DeliveryResult
from monetization.channels.telegram_channel import TelegramChannel
from monetization.channels.webhook_channel import WebhookChannel
from monetization.channels.rss_channel import RSSChannel

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fixtures.conftest import make_opportunity_event


# ═══════════════════════════════════════════════════════════════
# 1. Telegram Channel
# ═══════════════════════════════════════════════════════════════


class TestTelegramFormatting:

    def test_message_contains_product_name(self):
        ch = TelegramChannel(chat_ids=["123"], bot_token="test-token")
        event = make_opportunity_event(product_name="Sony WH-1000XM4")
        msg = ch._format_message(event)
        assert "Sony WH-1000XM4" in msg

    def test_message_contains_prices(self):
        ch = TelegramChannel(chat_ids=["123"], bot_token="test-token")
        event = make_opportunity_event(buy_price=100.0, sell_price=200.0)
        msg = ch._format_message(event)
        assert "$100.00" in msg
        assert "$200.00" in msg

    def test_message_contains_profit(self):
        ch = TelegramChannel(chat_ids=["123"], bot_token="test-token")
        event = make_opportunity_event(net_profit=45.0, roi=0.45)
        msg = ch._format_message(event)
        assert "$45.00" in msg
        assert "45.0%" in msg

    def test_risk_badge_prime(self):
        ch = TelegramChannel(chat_ids=["123"], bot_token="test-token")
        event = make_opportunity_event(risk_class="prime")
        msg = ch._format_message(event)
        assert "PRIME" in msg

    def test_emoji_based_on_roi(self):
        ch = TelegramChannel(chat_ids=["123"], bot_token="test-token")
        high_roi = make_opportunity_event(roi=0.55)
        msg = ch._format_message(high_roi)
        assert "🔥" in msg

    def test_contains_buy_url(self):
        ch = TelegramChannel(chat_ids=["123"], bot_token="test-token")
        event = make_opportunity_event(buy_url="https://example.com/buy")
        msg = ch._format_message(event)
        assert "https://example.com/buy" in msg


class TestTelegramDelivery:

    @pytest.mark.asyncio
    async def test_no_token_returns_failure(self):
        ch = TelegramChannel(chat_ids=["123"], bot_token="")
        event = make_opportunity_event()
        result = await ch.deliver(event)
        assert result.success is False
        assert "No bot token" in result.error

    @pytest.mark.asyncio
    async def test_no_chat_ids_returns_failure(self):
        ch = TelegramChannel(chat_ids=[], bot_token="test-token")
        event = make_opportunity_event()
        result = await ch.deliver(event)
        assert result.success is False


# ═══════════════════════════════════════════════════════════════
# 2. Webhook Channel
# ═══════════════════════════════════════════════════════════════


class TestWebhookPayload:

    def test_payload_structure(self):
        ch = WebhookChannel(webhook_urls=["https://hooks.example.com"])
        event = make_opportunity_event()
        payload = ch._format_payload(event)
        assert "event_type" in payload
        assert "opportunity" in payload
        assert "timestamp" in payload

        opp = payload["opportunity"]
        assert opp["id"] == event.opportunity_id
        assert opp["product_name"] == event.product_name
        assert opp["buy_price"] == event.buy_price
        assert opp["sell_price"] == event.sell_price
        assert opp["roi"] == event.roi
        assert opp["risk_class"] == event.risk_class

    def test_payload_contains_all_fields(self):
        ch = WebhookChannel(webhook_urls=["https://hooks.example.com"])
        event = make_opportunity_event(category="Headphones")
        event.image_url = "https://img.example.com/1.jpg"
        payload = ch._format_payload(event)
        opp = payload["opportunity"]
        assert opp["category"] == "Headphones"
        assert opp["image_url"] == "https://img.example.com/1.jpg"


class TestWebhookDelivery:

    @pytest.mark.asyncio
    async def test_no_urls_returns_failure(self):
        ch = WebhookChannel(webhook_urls=[])
        event = make_opportunity_event()
        result = await ch.deliver(event)
        assert result.success is False
        assert "No webhook URLs" in result.error


# ═══════════════════════════════════════════════════════════════
# 3. RSS Channel
# ═══════════════════════════════════════════════════════════════


class TestRSSChannel:

    @pytest.mark.asyncio
    async def test_deliver_accumulates_items(self):
        rss = RSSChannel(max_items=100)
        event = make_opportunity_event()
        result = await rss.deliver(event)
        assert result.success is True
        assert rss.item_count == 1

    @pytest.mark.asyncio
    async def test_max_items_cap(self):
        rss = RSSChannel(max_items=5)
        for i in range(10):
            await rss.deliver(make_opportunity_event(opportunity_id=i))
        assert rss.item_count == 5

    @pytest.mark.asyncio
    async def test_generate_xml_valid(self):
        rss = RSSChannel(
            title="Test Feed",
            description="Test description",
            link="http://localhost",
        )
        await rss.deliver(make_opportunity_event(
            product_name="Sony Headphones",
            net_profit=45.0,
        ))
        xml = rss.generate_xml()
        assert '<?xml version="1.0"' in xml
        assert "<rss" in xml
        assert "<channel>" in xml
        assert "<item>" in xml
        assert "Test Feed" in xml

    @pytest.mark.asyncio
    async def test_xml_contains_opportunity_data(self):
        rss = RSSChannel()
        await rss.deliver(make_opportunity_event(
            product_name="AirPods Pro",
            net_profit=30.0,
            roi=0.25,
        ))
        xml = rss.generate_xml()
        assert "$30 profit" in xml
        assert "AirPods Pro" in xml
        assert "ROI 25.0%" in xml

    @pytest.mark.asyncio
    async def test_xml_escapes_html(self):
        rss = RSSChannel()
        await rss.deliver(make_opportunity_event(
            product_name="Product <script>alert(1)</script>",
        ))
        xml = rss.generate_xml()
        assert "<script>" not in xml
        assert "&lt;script&gt;" in xml

    @pytest.mark.asyncio
    async def test_empty_feed(self):
        rss = RSSChannel()
        xml = rss.generate_xml()
        assert "<rss" in xml
        assert "<item>" not in xml

    @pytest.mark.asyncio
    async def test_items_in_reverse_order(self):
        rss = RSSChannel()
        await rss.deliver(make_opportunity_event(opportunity_id=1, product_name="First"))
        await rss.deliver(make_opportunity_event(opportunity_id=2, product_name="Second"))
        xml = rss.generate_xml()
        # Reversed: Second should appear before First
        assert xml.index("Second") < xml.index("First")
