"""
Unit Tests — Opportunity Publisher.

Tests:
  - Channel registration and unregistration
  - Event fan-out to matching channels
  - Filter rules (score, ROI, risk class, event type, marketplace)
  - Error isolation (failed channel doesn't crash publisher)
  - Duplicate prevention via event log
  - DeliveryResult tracking
"""
import asyncio

import pytest

from monetization.publisher import (
    OpportunityPublisher,
    OpportunityEvent,
    ChannelAdapter,
    ChannelFilter,
    DeliveryResult,
    EventType,
)

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fixtures.conftest import make_opportunity_event


# ── Test Channel Adapter ─────────────────────────────────────


class MockChannel(ChannelAdapter):
    """In-memory channel for testing."""

    def __init__(self, name="mock", filter_rules=None, should_fail=False):
        super().__init__(name, filter_rules)
        self.should_fail = should_fail
        self.received: list[OpportunityEvent] = []

    async def deliver(self, event: OpportunityEvent) -> DeliveryResult:
        if self.should_fail:
            raise RuntimeError("Channel delivery failed")
        self.received.append(event)
        return DeliveryResult(self.name, True)


# ═══════════════════════════════════════════════════════════════
# 1. Channel Registration
# ═══════════════════════════════════════════════════════════════


class TestChannelRegistration:

    def test_register_channel(self):
        pub = OpportunityPublisher()
        ch = MockChannel("test")
        pub.register_channel(ch)
        assert len(pub.channels) == 1
        assert pub.channels[0]["name"] == "test"

    def test_unregister_channel(self):
        pub = OpportunityPublisher()
        pub.register_channel(MockChannel("a"))
        pub.register_channel(MockChannel("b"))
        pub.unregister_channel("a")
        assert len(pub.channels) == 1
        assert pub.channels[0]["name"] == "b"

    def test_unregister_nonexistent_no_error(self):
        pub = OpportunityPublisher()
        pub.unregister_channel("nonexistent")  # Should not raise


# ═══════════════════════════════════════════════════════════════
# 2. Event Publishing
# ═══════════════════════════════════════════════════════════════


class TestEventPublishing:

    @pytest.mark.asyncio
    async def test_delivers_to_matching_channel(self):
        pub = OpportunityPublisher()
        ch = MockChannel("test")
        pub.register_channel(ch)

        event = make_opportunity_event()
        results = await pub.publish(event)
        assert len(results) == 1
        assert results[0].success is True
        assert len(ch.received) == 1

    @pytest.mark.asyncio
    async def test_fan_out_to_multiple_channels(self):
        pub = OpportunityPublisher()
        ch1 = MockChannel("ch1")
        ch2 = MockChannel("ch2")
        pub.register_channel(ch1)
        pub.register_channel(ch2)

        event = make_opportunity_event()
        results = await pub.publish(event)
        assert len(results) == 2
        assert len(ch1.received) == 1
        assert len(ch2.received) == 1

    @pytest.mark.asyncio
    async def test_disabled_channel_skipped(self):
        pub = OpportunityPublisher()
        ch = MockChannel("test")
        ch.disable()
        pub.register_channel(ch)

        event = make_opportunity_event()
        results = await pub.publish(event)
        assert len(results) == 0
        assert len(ch.received) == 0

    @pytest.mark.asyncio
    async def test_no_channels_empty_results(self):
        pub = OpportunityPublisher()
        event = make_opportunity_event()
        results = await pub.publish(event)
        assert results == []


# ═══════════════════════════════════════════════════════════════
# 3. Filter Rules
# ═══════════════════════════════════════════════════════════════


class TestChannelFilter:

    def test_matches_default(self):
        f = ChannelFilter()
        event = make_opportunity_event()
        assert f.matches(event) is True

    def test_min_score_filter(self):
        f = ChannelFilter(min_opportunity_score=80.0)
        low_score = make_opportunity_event(opportunity_score=50.0)
        high_score = make_opportunity_event(opportunity_score=85.0)
        assert f.matches(low_score) is False
        assert f.matches(high_score) is True

    def test_min_roi_filter(self):
        f = ChannelFilter(min_roi=0.30)
        low_roi = make_opportunity_event(roi=0.10)
        high_roi = make_opportunity_event(roi=0.40)
        assert f.matches(low_roi) is False
        assert f.matches(high_roi) is True

    def test_min_profit_filter(self):
        f = ChannelFilter(min_profit=50.0)
        low = make_opportunity_event(net_profit=20.0)
        high = make_opportunity_event(net_profit=60.0)
        assert f.matches(low) is False
        assert f.matches(high) is True

    def test_max_risk_filter(self):
        f = ChannelFilter(max_risk_score=40.0)
        safe = make_opportunity_event(risk_score=25.0)
        risky = make_opportunity_event(risk_score=60.0)
        assert f.matches(safe) is True
        assert f.matches(risky) is False

    def test_risk_class_filter(self):
        f = ChannelFilter(allowed_risk_classes=["prime", "standard"])
        prime = make_opportunity_event(risk_class="prime")
        avoid = make_opportunity_event(risk_class="avoid")
        assert f.matches(prime) is True
        assert f.matches(avoid) is False

    def test_event_type_filter(self):
        f = ChannelFilter(event_types=["price_alert"])
        opp = make_opportunity_event(event_type="new_opportunity")
        alert = make_opportunity_event(event_type="price_alert")
        assert f.matches(opp) is False
        assert f.matches(alert) is True

    def test_marketplace_filter(self):
        f = ChannelFilter(marketplaces=["amazon"])
        amazon = make_opportunity_event(buy_marketplace="amazon")
        ebay = make_opportunity_event(buy_marketplace="ebay", sell_marketplace="ebay")
        assert f.matches(amazon) is True
        assert f.matches(ebay) is False

    def test_empty_marketplace_allows_all(self):
        f = ChannelFilter(marketplaces=[])
        event = make_opportunity_event(buy_marketplace="ebay")
        assert f.matches(event) is True

    @pytest.mark.asyncio
    async def test_filtered_channel_not_delivered(self):
        pub = OpportunityPublisher()
        ch = MockChannel(
            "filtered",
            filter_rules=ChannelFilter(min_opportunity_score=90.0),
        )
        pub.register_channel(ch)

        event = make_opportunity_event(opportunity_score=50.0)
        results = await pub.publish(event)
        assert len(results) == 0
        assert len(ch.received) == 0


# ═══════════════════════════════════════════════════════════════
# 4. Error Isolation
# ═══════════════════════════════════════════════════════════════


class TestErrorIsolation:

    @pytest.mark.asyncio
    async def test_failing_channel_doesnt_crash_others(self):
        pub = OpportunityPublisher()
        failing = MockChannel("failing", should_fail=True)
        healthy = MockChannel("healthy")
        pub.register_channel(failing)
        pub.register_channel(healthy)

        event = make_opportunity_event()
        results = await pub.publish(event)
        assert len(results) == 2

        failed_result = next(r for r in results if r.channel_name == "failing")
        ok_result = next(r for r in results if r.channel_name == "healthy")
        assert failed_result.success is False
        assert ok_result.success is True
        assert len(healthy.received) == 1

    @pytest.mark.asyncio
    async def test_failed_delivery_tracked_in_stats(self):
        pub = OpportunityPublisher()
        failing = MockChannel("failing", should_fail=True)
        pub.register_channel(failing)

        event = make_opportunity_event()
        await pub.publish(event)
        assert failing.stats["failed"] == 1
        assert failing.stats["delivered"] == 0

    @pytest.mark.asyncio
    async def test_successful_delivery_tracked(self):
        pub = OpportunityPublisher()
        ch = MockChannel("test")
        pub.register_channel(ch)

        await pub.publish(make_opportunity_event())
        await pub.publish(make_opportunity_event(opportunity_id=2))
        assert ch.stats["delivered"] == 2


# ═══════════════════════════════════════════════════════════════
# 5. Event Log
# ═══════════════════════════════════════════════════════════════


class TestEventLog:

    @pytest.mark.asyncio
    async def test_recent_events_tracked(self):
        pub = OpportunityPublisher()
        pub.register_channel(MockChannel("test"))

        await pub.publish(make_opportunity_event(opportunity_id=1))
        await pub.publish(make_opportunity_event(opportunity_id=2))

        recent = pub.recent_events
        assert len(recent) == 2
        assert recent[0]["opportunity_id"] == 1

    @pytest.mark.asyncio
    async def test_event_log_caps_at_max(self):
        pub = OpportunityPublisher()
        pub._max_log_size = 10
        pub.register_channel(MockChannel("test"))

        for i in range(15):
            await pub.publish(make_opportunity_event(opportunity_id=i))

        # Log should be trimmed
        assert len(pub._event_log) <= 10
