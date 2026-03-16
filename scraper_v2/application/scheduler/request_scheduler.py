"""Request Scheduler — orchestrates task generation and distribution.

Generates CrawlTasks from category config, search terms, and trending pages,
then feeds them into the task queue with proper prioritization and rate limiting.
"""

from __future__ import annotations

import logging
import random
import uuid
from dataclasses import dataclass

from scraper_v2.domain.enums import CrawlMode, Marketplace, TaskPriority
from scraper_v2.domain.models import CrawlTask
from scraper_v2.domain.ports import TaskQueuePort

from .category_priority import CategoryPriorityStrategy
from .rate_limiter import AdaptiveRateLimiter

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    weight_category: float = 0.60
    weight_search: float = 0.25
    weight_trending: float = 0.15
    max_tasks_per_cycle: int = 500
    shuffle_tasks: bool = True


class RequestScheduler:
    """Generates and schedules crawl tasks across marketplaces."""

    def __init__(
        self,
        task_queue: TaskQueuePort,
        rate_limiter: AdaptiveRateLimiter,
        category_priority: CategoryPriorityStrategy,
        category_urls: dict[Marketplace, dict[str, str]],
        search_terms: list[str],
        trending_urls: dict[Marketplace, list[str]],
        config: SchedulerConfig | None = None,
    ) -> None:
        self._queue = task_queue
        self._rate_limiter = rate_limiter
        self._priority = category_priority
        self._category_urls = category_urls
        self._search_terms = search_terms
        self._trending_urls = trending_urls
        self._config = config or SchedulerConfig()

    async def generate_cycle(self, marketplaces: list[Marketplace]) -> int:
        """Generate tasks for one full scraping cycle. Returns task count."""
        tasks: list[CrawlTask] = []
        budget = self._config.max_tasks_per_cycle

        cat_budget = int(budget * self._config.weight_category)
        search_budget = int(budget * self._config.weight_search)
        trending_budget = budget - cat_budget - search_budget

        tasks.extend(self._generate_category_tasks(marketplaces, cat_budget))
        tasks.extend(self._generate_search_tasks(marketplaces, search_budget))
        tasks.extend(self._generate_trending_tasks(marketplaces, trending_budget))

        if self._config.shuffle_tasks:
            random.shuffle(tasks)

        # Stable sort by priority (preserves shuffle within same priority)
        tasks.sort(key=lambda t: t.priority.value)

        for task in tasks:
            await self._queue.enqueue(task)

        logger.info(
            "Scheduled %d tasks (cat=%d, search=%d, trending=%d)",
            len(tasks),
            cat_budget,
            search_budget,
            trending_budget,
        )
        return len(tasks)

    def _generate_category_tasks(
        self, marketplaces: list[Marketplace], budget: int
    ) -> list[CrawlTask]:
        tasks: list[CrawlTask] = []
        all_categories = set()
        for mp in marketplaces:
            all_categories.update(self._category_urls.get(mp, {}).keys())

        prioritized = self._priority.get_prioritized(list(all_categories))

        for category in prioritized:
            if len(tasks) >= budget:
                break
            for mp in marketplaces:
                if len(tasks) >= budget:
                    break
                urls = self._category_urls.get(mp, {})
                url = urls.get(category)
                if not url:
                    continue
                max_pages = self._priority.get_pages(category)
                for page in range(1, max_pages + 1):
                    if len(tasks) >= budget:
                        break
                    tasks.append(
                        CrawlTask(
                            task_id=f"cat-{uuid.uuid4().hex[:8]}",
                            marketplace=mp,
                            mode=CrawlMode.CATEGORY,
                            url=url,
                            category=category,
                            page=page,
                            max_pages=max_pages,
                            priority=TaskPriority.NORMAL,
                        )
                    )
        return tasks

    def _generate_search_tasks(
        self, marketplaces: list[Marketplace], budget: int
    ) -> list[CrawlTask]:
        tasks: list[CrawlTask] = []
        terms = list(self._search_terms)
        random.shuffle(terms)

        for term in terms:
            if len(tasks) >= budget:
                break
            for mp in marketplaces:
                if len(tasks) >= budget:
                    break
                search_url = self._build_search_url(mp, term)
                if not search_url:
                    continue
                tasks.append(
                    CrawlTask(
                        task_id=f"srch-{uuid.uuid4().hex[:8]}",
                        marketplace=mp,
                        mode=CrawlMode.SEARCH,
                        url=search_url,
                        search_term=term,
                        priority=TaskPriority.NORMAL,
                    )
                )
        return tasks

    def _generate_trending_tasks(
        self, marketplaces: list[Marketplace], budget: int
    ) -> list[CrawlTask]:
        tasks: list[CrawlTask] = []
        for mp in marketplaces:
            if len(tasks) >= budget:
                break
            for url in self._trending_urls.get(mp, []):
                if len(tasks) >= budget:
                    break
                tasks.append(
                    CrawlTask(
                        task_id=f"trend-{uuid.uuid4().hex[:8]}",
                        marketplace=mp,
                        mode=CrawlMode.TRENDING,
                        url=url,
                        priority=TaskPriority.HIGH,
                    )
                )
        return tasks

    @staticmethod
    def _build_search_url(marketplace: Marketplace, term: str) -> str | None:
        encoded = term.replace(" ", "-")
        encoded_plus = term.replace(" ", "+")
        urls: dict[Marketplace, str] = {
            Marketplace.MERCADOLIBRE_AR: f"https://listado.mercadolibre.com.ar/{encoded}",
            Marketplace.MERCADOLIBRE_MX: f"https://listado.mercadolibre.com.mx/{encoded}",
            Marketplace.MERCADOLIBRE_CL: f"https://listado.mercadolibre.cl/{encoded}",
            Marketplace.MERCADOLIBRE_CO: f"https://listado.mercadolibre.com.co/{encoded}",
            Marketplace.AMAZON_MX: f"https://www.amazon.com.mx/s?k={encoded_plus}",
            Marketplace.AMAZON_US: f"https://www.amazon.com/s?k={encoded_plus}",
            Marketplace.EBAY: f"https://www.ebay.com/sch/i.html?_nkw={encoded_plus}",
        }
        return urls.get(marketplace)
