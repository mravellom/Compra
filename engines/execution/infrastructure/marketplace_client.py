"""
Abstract Marketplace Client — interface for all marketplace integrations.

Each marketplace implements this to enable real order execution.
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MarketplaceOrderResult:
    """Result from placing an order on a marketplace."""
    success: bool
    marketplace_order_id: Optional[str] = None
    executed_price: Optional[float] = None
    fees: float = 0.0
    error_message: Optional[str] = None
    status: str = "unknown"  # created, confirmed, pending, failed
    metadata: dict = field(default_factory=dict)


@dataclass
class MarketplaceItemStatus:
    """Current status of an item/listing on a marketplace."""
    available: bool = False
    current_price: Optional[float] = None
    stock: int = 0
    seller_name: Optional[str] = None
    condition: Optional[str] = None
    listing_id: Optional[str] = None


class MarketplaceClient(ABC):
    """Abstract base for marketplace API integrations.

    Each marketplace (MercadoLibre, Amazon, eBay, etc.) implements this
    interface to enable real trade execution.
    """

    @property
    @abstractmethod
    def marketplace_id(self) -> str:
        """Identifier for this marketplace (e.g., 'mercadolibre_ar')."""
        ...

    @abstractmethod
    async def place_buy_order(
        self,
        listing_url: str,
        quantity: int = 1,
        max_price: Optional[float] = None,
    ) -> MarketplaceOrderResult:
        """Place a buy order on the marketplace.

        Args:
            listing_url: URL of the listing to purchase.
            quantity: Number of units.
            max_price: Maximum acceptable price (reject if current > max).
        """
        ...

    @abstractmethod
    async def place_sell_listing(
        self,
        title: str,
        price: float,
        quantity: int = 1,
        category_id: Optional[str] = None,
        condition: str = "new",
        description: str = "",
        images: Optional[list[str]] = None,
    ) -> MarketplaceOrderResult:
        """Create a sell listing on the marketplace.

        Args:
            title: Product title.
            price: Listing price.
            quantity: Available quantity.
            category_id: Marketplace category.
            condition: Item condition (new, used, refurbished).
            description: Listing description.
            images: Image URLs.
        """
        ...

    @abstractmethod
    async def get_order_status(
        self,
        marketplace_order_id: str,
    ) -> MarketplaceOrderResult:
        """Check the current status of an order."""
        ...

    @abstractmethod
    async def get_item_status(
        self,
        listing_url: str,
    ) -> MarketplaceItemStatus:
        """Get current price and availability for a listing."""
        ...

    @abstractmethod
    async def cancel_order(
        self,
        marketplace_order_id: str,
    ) -> bool:
        """Cancel a pending order. Returns True if cancelled."""
        ...

    async def health_check(self) -> bool:
        """Check if the marketplace API is reachable."""
        return True
