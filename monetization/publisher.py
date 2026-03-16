"""
Opportunity Publisher — Event-driven monetization system.

Architecture:
  OpportunityEvent → Publisher → [Channel Adapters]

  ┌─────────────────┐     ┌───────────────┐     ┌─────────────────┐
  │ Opportunity      │     │   Publisher    │     │  Telegram Bot   │
  │ Engine           │────▶│   (fan-out)   │────▶│  Webhook        │
  │ (detect/score)   │     │               │────▶│  RSS Feed       │
  └─────────────────┘     └───────────────┘     │  Email          │
                                                 └─────────────────┘

Patterns:
  - Publisher/Subscriber: decoupled event distribution
  - Adapter: each channel has its own format/protocol
  - Strategy: filtering rules per channel

Event Flow:
  1. Opportunity scored → OpportunityEvent created
  2. Publisher filters by channel rules (min score, profile match)
  3. Each active channel adapter formats and delivers
  4. Delivery results logged for analytics
"""
import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

from api.scoring_profiles import RiskClass

logger = logging.getLogger(__name__)


class EventType(Enum):
    NEW_OPPORTUNITY = "new_opportunity"
    OPPORTUNITY_UPDATE = "opportunity_update"
    OPPORTUNITY_EXPIRED = "opportunity_expired"
    PRICE_ALERT = "price_alert"
    TREND_BREAKOUT = "trend_breakout"


@dataclass
class OpportunityEvent:
    """Canonical event for opportunity publishing."""
    event_type: str
    opportunity_id: int
    product_name: str
    buy_marketplace: str
    sell_marketplace: str
    route: str
    buy_price: float
    sell_price: float
    net_profit: float
    roi: float
    opportunity_score: float
    risk_score: float
    confidence_level: str
    risk_class: str
    buy_url: str = ""
    sell_url: str = ""
    image_url: str | None = None
    category: str | None = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class ChannelFilter:
    """Rules for what events a channel receives."""
    min_opportunity_score: float = 0.0
    min_roi: float = 0.0
    min_profit: float = 0.0
    max_risk_score: float = 100.0
    allowed_risk_classes: list[str] = field(default_factory=lambda: ["prime", "standard", "speculative"])
    event_types: list[str] = field(default_factory=lambda: ["new_opportunity"])
    marketplaces: list[str] = field(default_factory=list)  # Empty = all

    def matches(self, event: OpportunityEvent) -> bool:
        if event.event_type not in self.event_types:
            return False
        if event.opportunity_score < self.min_opportunity_score:
            return False
        if event.roi < self.min_roi:
            return False
        if event.net_profit < self.min_profit:
            return False
        if event.risk_score > self.max_risk_score:
            return False
        if event.risk_class not in self.allowed_risk_classes:
            return False
        if self.marketplaces:
            if event.buy_marketplace not in self.marketplaces and event.sell_marketplace not in self.marketplaces:
                return False
        return True


@dataclass
class DeliveryResult:
    channel_name: str
    success: bool
    error: str | None = None
    latency_ms: float = 0.0


# ── Abstract Channel Adapter ─────────────────────────────────

class ChannelAdapter(ABC):
    """Base class for all monetization channels."""

    def __init__(self, name: str, filter_rules: ChannelFilter | None = None):
        self.name = name
        self.filter = filter_rules or ChannelFilter()
        self._enabled = True
        self._delivered = 0
        self._failed = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    @abstractmethod
    async def deliver(self, event: OpportunityEvent) -> DeliveryResult:
        """Format and deliver the event through this channel."""
        ...

    @property
    def stats(self) -> dict:
        return {
            "name": self.name,
            "enabled": self._enabled,
            "delivered": self._delivered,
            "failed": self._failed,
        }


# ── Publisher ─────────────────────────────────────────────────

class OpportunityPublisher:
    """
    Fan-out publisher that distributes events to all registered channels.

    Thread-safe, async. Channels are evaluated in parallel.
    Failed deliveries are logged but don't block other channels.
    """

    def __init__(self) -> None:
        self._channels: list[ChannelAdapter] = []
        self._event_log: list[tuple[OpportunityEvent, list[DeliveryResult]]] = []
        self._max_log_size = 1000

    def register_channel(self, channel: ChannelAdapter) -> None:
        self._channels.append(channel)
        logger.info("[Publisher] Registered channel: %s", channel.name)

    def unregister_channel(self, name: str) -> None:
        self._channels = [c for c in self._channels if c.name != name]

    async def publish(self, event: OpportunityEvent) -> list[DeliveryResult]:
        """Publish event to all matching channels in parallel."""
        results: list[DeliveryResult] = []

        tasks = []
        for channel in self._channels:
            if not channel.enabled:
                continue
            if not channel.filter.matches(event):
                continue
            tasks.append(self._deliver_safe(channel, event))

        if tasks:
            results = await asyncio.gather(*tasks)
            self._log_event(event, results)

            delivered = sum(1 for r in results if r.success)
            failed = sum(1 for r in results if not r.success)
            if delivered > 0 or failed > 0:
                logger.info(
                    "[Publisher] Event %s opp#%d → %d delivered, %d failed",
                    event.event_type, event.opportunity_id, delivered, failed,
                )

        return results

    async def _deliver_safe(
        self, channel: ChannelAdapter, event: OpportunityEvent
    ) -> DeliveryResult:
        """Deliver to a single channel with error isolation."""
        t0 = time.monotonic()
        try:
            result = await channel.deliver(event)
            result.latency_ms = (time.monotonic() - t0) * 1000
            if result.success:
                channel._delivered += 1
            else:
                channel._failed += 1
            return result
        except Exception as e:
            channel._failed += 1
            return DeliveryResult(
                channel_name=channel.name,
                success=False,
                error=str(e),
                latency_ms=(time.monotonic() - t0) * 1000,
            )

    def _log_event(self, event: OpportunityEvent, results: list[DeliveryResult]) -> None:
        self._event_log.append((event, results))
        if len(self._event_log) > self._max_log_size:
            self._event_log = self._event_log[-self._max_log_size // 2:]

    @property
    def channels(self) -> list[dict]:
        return [c.stats for c in self._channels]

    @property
    def recent_events(self) -> list[dict]:
        return [
            {
                "event_type": e.event_type,
                "opportunity_id": e.opportunity_id,
                "product": e.product_name[:50],
                "channels": len(results),
                "delivered": sum(1 for r in results if r.success),
                "timestamp": e.timestamp,
            }
            for e, results in self._event_log[-20:]
        ]
