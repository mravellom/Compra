"""
Category prioritization based on historical opportunity yield.

Tracks which categories produce the most arbitrage opportunities and
dynamically adjusts scraping depth (pages per category) to maximize
the discovery of profitable opportunities.

Data is persisted to a JSON file between runs.
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Persistence file for category stats
_STATS_FILE = os.getenv(
    "CATEGORY_STATS_FILE",
    os.path.join(os.path.dirname(__file__), ".category_stats.json"),
)

# Prioritization thresholds
HIGH_YIELD_THRESHOLD = 15      # opportunities → boost pages
LOW_YIELD_THRESHOLD = 2        # opportunities → reduce pages
BOOST_MULTIPLIER = 2.0         # multiply pages for high-yield
REDUCE_MULTIPLIER = 0.5        # multiply pages for low-yield
MIN_PAGES = 3                  # never go below this
MAX_PAGES = 50                 # never exceed this

# Decay factor — reduce stats weight over time (exponential decay per cycle)
DECAY_FACTOR = 0.9


@dataclass
class CategoryStats:
    """Accumulated stats for a single category."""
    total_listings: int = 0
    total_opportunities: int = 0
    total_profitable_opportunities: int = 0
    avg_roi: float = 0.0
    cycles_scraped: int = 0
    last_scraped_at: float = 0.0
    pages_allocated: int = 10

    def opportunity_score(self) -> float:
        """Score for prioritization: weighted combination of volume and profitability."""
        if self.cycles_scraped == 0:
            return 0.0
        opp_rate = self.total_opportunities / max(self.cycles_scraped, 1)
        profit_rate = self.total_profitable_opportunities / max(self.cycles_scraped, 1)
        return (opp_rate * 0.4) + (profit_rate * 0.6) + (self.avg_roi * 10)


class CategoryPrioritizer:
    """
    Dynamically allocates scraping depth per category based on
    historical opportunity discovery rates.
    """

    def __init__(self, base_pages: int = 10):
        self._base_pages = base_pages
        self._stats: dict[str, CategoryStats] = {}
        self._load()

    def _load(self) -> None:
        """Load persisted stats from disk."""
        if not os.path.exists(_STATS_FILE):
            return
        try:
            with open(_STATS_FILE, "r") as f:
                data = json.load(f)
            for slug, s in data.items():
                self._stats[slug] = CategoryStats(**s)
            logger.info("Loaded prioritization stats for %d categories", len(self._stats))
        except Exception:
            logger.warning("Failed to load category stats, starting fresh", exc_info=True)

    def _save(self) -> None:
        """Persist stats to disk."""
        try:
            data = {}
            for slug, s in self._stats.items():
                data[slug] = {
                    "total_listings": s.total_listings,
                    "total_opportunities": s.total_opportunities,
                    "total_profitable_opportunities": s.total_profitable_opportunities,
                    "avg_roi": s.avg_roi,
                    "cycles_scraped": s.cycles_scraped,
                    "last_scraped_at": s.last_scraped_at,
                    "pages_allocated": s.pages_allocated,
                }
            with open(_STATS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            logger.warning("Failed to save category stats", exc_info=True)

    def get_pages(self, category_slug: str) -> int:
        """Get the allocated page count for a category."""
        stats = self._stats.get(category_slug)
        if not stats:
            return self._base_pages
        return stats.pages_allocated

    def record_scrape(
        self,
        category_slug: str,
        listings_count: int,
        pages_scraped: int,
    ) -> None:
        """Record results from a category scrape."""
        if category_slug not in self._stats:
            self._stats[category_slug] = CategoryStats(pages_allocated=self._base_pages)

        stats = self._stats[category_slug]
        stats.total_listings += listings_count
        stats.cycles_scraped += 1
        stats.last_scraped_at = time.time()

    def record_opportunities(
        self,
        category_slug: str,
        opportunities_count: int,
        profitable_count: int = 0,
        avg_roi: float = 0.0,
    ) -> None:
        """Record opportunity discovery for a category (called by processor/API)."""
        if category_slug not in self._stats:
            self._stats[category_slug] = CategoryStats(pages_allocated=self._base_pages)

        stats = self._stats[category_slug]
        stats.total_opportunities += opportunities_count
        stats.total_profitable_opportunities += profitable_count
        if avg_roi > 0:
            # Running average
            if stats.avg_roi > 0:
                stats.avg_roi = (stats.avg_roi * 0.7) + (avg_roi * 0.3)
            else:
                stats.avg_roi = avg_roi

    def recalculate_priorities(self) -> None:
        """
        Recalculate page allocations for all categories based on
        accumulated opportunity data. Call this once per cycle.
        """
        if not self._stats:
            return

        # Apply decay to all stats
        for stats in self._stats.values():
            stats.total_listings = int(stats.total_listings * DECAY_FACTOR)
            stats.total_opportunities = int(stats.total_opportunities * DECAY_FACTOR)
            stats.total_profitable_opportunities = int(
                stats.total_profitable_opportunities * DECAY_FACTOR
            )

        # Calculate scores
        scores = {
            slug: stats.opportunity_score()
            for slug, stats in self._stats.items()
        }

        if not scores:
            return

        max_score = max(scores.values()) if scores else 1.0
        if max_score == 0:
            max_score = 1.0

        # Allocate pages based on relative score
        for slug, score in scores.items():
            stats = self._stats[slug]
            normalized = score / max_score  # 0.0 to 1.0

            if normalized > 0.7:
                # High yield → boost
                stats.pages_allocated = min(
                    int(self._base_pages * BOOST_MULTIPLIER), MAX_PAGES,
                )
            elif normalized < 0.2:
                # Low yield → reduce
                stats.pages_allocated = max(
                    int(self._base_pages * REDUCE_MULTIPLIER), MIN_PAGES,
                )
            else:
                # Normal
                stats.pages_allocated = self._base_pages

        self._save()

        # Log top/bottom categories
        sorted_cats = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top = sorted_cats[:5]
        bottom = sorted_cats[-3:] if len(sorted_cats) > 5 else []

        logger.info(
            "Category priorities recalculated: top=%s bottom=%s",
            [(s, f"{sc:.1f}") for s, sc in top],
            [(s, f"{sc:.1f}") for s, sc in bottom],
        )

    def get_prioritized_categories(
        self,
        all_categories: list[str],
    ) -> list[str]:
        """
        Return categories ordered by priority (highest-yield first).
        Unknown categories are placed in the middle.
        """
        scored: list[tuple[str, float]] = []
        unscored: list[str] = []

        for slug in all_categories:
            stats = self._stats.get(slug)
            if stats and stats.cycles_scraped > 0:
                scored.append((slug, stats.opportunity_score()))
            else:
                unscored.append(slug)

        # High-score first
        scored.sort(key=lambda x: x[1], reverse=True)
        result = [slug for slug, _ in scored]

        # Insert unscored in the middle (give them a fair chance)
        mid = len(result) // 2
        result = result[:mid] + unscored + result[mid:]

        return result

    @property
    def stats_summary(self) -> dict[str, dict]:
        """Get a summary of all category stats."""
        return {
            slug: {
                "score": stats.opportunity_score(),
                "pages": stats.pages_allocated,
                "opportunities": stats.total_opportunities,
                "cycles": stats.cycles_scraped,
            }
            for slug, stats in self._stats.items()
        }
