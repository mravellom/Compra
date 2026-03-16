"""Bulkhead — isolates concurrent work per marketplace.

Prevents one slow/failing marketplace from consuming all worker capacity.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from scraper_v2.domain.enums import Marketplace

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BulkheadConfig:
    max_concurrent_per_marketplace: int = 4
    max_concurrent_total: int = 20
    queue_size: int = 100


class Bulkhead:
    """Semaphore-based bulkhead for marketplace isolation."""

    def __init__(self, config: BulkheadConfig | None = None) -> None:
        self._config = config or BulkheadConfig()
        self._marketplace_semas: dict[Marketplace, asyncio.Semaphore] = {}
        self._global_sema = asyncio.Semaphore(self._config.max_concurrent_total)

    def _get_sema(self, marketplace: Marketplace) -> asyncio.Semaphore:
        if marketplace not in self._marketplace_semas:
            self._marketplace_semas[marketplace] = asyncio.Semaphore(
                self._config.max_concurrent_per_marketplace
            )
        return self._marketplace_semas[marketplace]

    async def acquire(self, marketplace: Marketplace) -> bool:
        """Acquire both global and per-marketplace slots. Returns False if full."""
        try:
            await asyncio.wait_for(self._global_sema.acquire(), timeout=0.1)
        except asyncio.TimeoutError:
            return False
        try:
            await asyncio.wait_for(self._get_sema(marketplace).acquire(), timeout=0.1)
        except asyncio.TimeoutError:
            self._global_sema.release()
            return False
        return True

    def release(self, marketplace: Marketplace) -> None:
        self._get_sema(marketplace).release()
        self._global_sema.release()

    def available(self, marketplace: Marketplace) -> int:
        sema = self._get_sema(marketplace)
        return sema._value  # type: ignore[attr-defined]

    def snapshot(self) -> dict[str, int]:
        return {
            mp.value: self.available(mp)
            for mp in self._marketplace_semas
        }
