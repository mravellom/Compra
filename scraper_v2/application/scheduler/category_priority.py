"""Category priority strategy — allocates scraping depth by profitability.

High-yield categories get more pages, low-yield get fewer.
Uses exponential decay to weight recent performance.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from scraper_v2.domain.models import CategoryStats

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PriorityConfig:
    min_pages: int = 3
    max_pages: int = 50
    default_pages: int = 15
    decay_factor: float = 0.9
    boost_threshold: float = 0.7
    boost_multiplier: float = 2.0
    reduce_threshold: float = 0.2
    reduce_multiplier: float = 0.5
    stats_file: str = ".category_stats_v2.json"


class CategoryPriorityStrategy:
    """Dynamically allocates scraping pages based on opportunity yield."""

    def __init__(self, config: PriorityConfig | None = None) -> None:
        self._config = config or PriorityConfig()
        self._stats: dict[str, CategoryStats] = {}
        self._load()

    def get_pages(self, category: str) -> int:
        stats = self._stats.get(category)
        if not stats:
            return self._config.default_pages
        return stats.allocated_pages

    def record_scrape(self, category: str, listings: int, pages: int) -> None:
        stats = self._get_or_create(category)
        stats.total_listings = int(stats.total_listings * self._config.decay_factor + listings)
        stats.cycles_scraped += 1

    def record_opportunities(
        self, category: str, count: int, avg_roi: float
    ) -> None:
        stats = self._get_or_create(category)
        stats.opportunities_found = int(
            stats.opportunities_found * self._config.decay_factor + count
        )
        stats.avg_roi = stats.avg_roi * self._config.decay_factor + avg_roi * (
            1 - self._config.decay_factor
        )

    def recalculate(self) -> None:
        """Recalculate all category priorities and page allocations."""
        if not self._stats:
            return

        scores = {
            cat: self._score(s) for cat, s in self._stats.items()
        }
        max_score = max(scores.values()) if scores else 1.0
        max_score = max(max_score, 0.01)

        for cat, score in scores.items():
            normalized = score / max_score
            stats = self._stats[cat]
            stats.priority_score = normalized

            if normalized >= self._config.boost_threshold:
                pages = int(self._config.default_pages * self._config.boost_multiplier)
            elif normalized <= self._config.reduce_threshold:
                pages = int(self._config.default_pages * self._config.reduce_multiplier)
            else:
                pages = self._config.default_pages

            stats.allocated_pages = max(
                self._config.min_pages, min(self._config.max_pages, pages)
            )

        self._save()

    def get_prioritized(self, categories: list[str]) -> list[str]:
        """Return categories sorted by priority score (highest first)."""
        for cat in categories:
            self._get_or_create(cat)
        return sorted(
            categories,
            key=lambda c: self._stats.get(c, CategoryStats(category=c)).priority_score,
            reverse=True,
        )

    def _score(self, stats: CategoryStats) -> float:
        opp_rate = stats.opportunity_rate
        return (opp_rate * 0.4) + (stats.avg_roi * 0.6)

    def _get_or_create(self, category: str) -> CategoryStats:
        if category not in self._stats:
            self._stats[category] = CategoryStats(
                category=category,
                allocated_pages=self._config.default_pages,
            )
        return self._stats[category]

    def _load(self) -> None:
        path = Path(self._config.stats_file)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            for cat, d in data.items():
                self._stats[cat] = CategoryStats(
                    category=cat,
                    total_listings=d.get("total_listings", 0),
                    opportunities_found=d.get("opportunities_found", 0),
                    avg_roi=d.get("avg_roi", 0.0),
                    cycles_scraped=d.get("cycles_scraped", 0),
                    allocated_pages=d.get("allocated_pages", 15),
                    priority_score=d.get("priority_score", 0.5),
                )
        except Exception as exc:
            logger.warning("Failed to load category stats: %s", exc)

    def _save(self) -> None:
        path = Path(self._config.stats_file)
        data = {
            cat: {
                "total_listings": s.total_listings,
                "opportunities_found": s.opportunities_found,
                "avg_roi": s.avg_roi,
                "cycles_scraped": s.cycles_scraped,
                "allocated_pages": s.allocated_pages,
                "priority_score": s.priority_score,
            }
            for cat, s in self._stats.items()
        }
        try:
            path.write_text(json.dumps(data, indent=2))
        except Exception as exc:
            logger.warning("Failed to save category stats: %s", exc)
