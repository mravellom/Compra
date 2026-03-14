"""
Automatic subcategory discovery.

When crawling a category page, this module extracts links to subcategories
and adds them to the scraping queue for deeper coverage.

Example:
    electronics/
    ├── audio/
    │   ├── headphones/
    │   ├── speakers/
    │   └── amplifiers/
    └── computers/
        ├── notebooks/
        └── monitors/
"""
import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Max depth to prevent infinite recursion
MAX_DISCOVERY_DEPTH = 3

# Patterns indicating a subcategory link (per marketplace)
_ML_SUBCATEGORY_SELECTORS = [
    # Sidebar category refinement links
    ".ui-search-filter-dl a",
    ".ui-search-filter-groups a",
    # Category breadcrumb children
    ".ui-search-breadcrumb a",
    # Category tree links
    '[class*="category"] a',
    # Department links in sidebar
    ".ui-search-filter-container a[href*='/listado.mercadolibre']",
]

_AMAZON_SUBCATEGORY_SELECTORS = [
    # Left sidebar category refinement
    "#departments ul li a",
    ".a-unordered-list .a-list-item a[href*='rh=']",
    # Category card links
    '[data-component-type="s-navigation-refinement"] a',
    # Sub-department links
    ".a-spacing-micro a[href*='&rh=']",
]

_ALIEXPRESS_SUBCATEGORY_SELECTORS = [
    # Category refinement links
    '[class*="refine"] a',
    '[class*="cate-item"] a',
    '[class*="sub-cate"] a',
]

# URL patterns to exclude (pagination, sorting, filters that aren't subcategories)
_EXCLUDE_PATTERNS = [
    r"page=",
    r"_Desde_",
    r"sort=",
    r"order=",
    r"#",
    r"javascript:",
    r"signin",
    r"login",
    r"cart",
    r"wishlist",
    r"account",
    r"help",
]
_EXCLUDE_RE = re.compile("|".join(_EXCLUDE_PATTERNS), re.IGNORECASE)


def _get_selectors(marketplace_id: str) -> list[str]:
    """Return CSS selectors for subcategory links based on marketplace."""
    if marketplace_id.startswith("mercadolibre"):
        return _ML_SUBCATEGORY_SELECTORS
    elif marketplace_id.startswith("amazon"):
        return _AMAZON_SUBCATEGORY_SELECTORS
    elif marketplace_id == "aliexpress":
        return _ALIEXPRESS_SUBCATEGORY_SELECTORS
    return []


def _is_valid_subcategory_url(url: str, base_domain: str) -> bool:
    """Check if a URL is a valid subcategory (not pagination/sort/external)."""
    if not url or len(url) < 10:
        return False
    if _EXCLUDE_RE.search(url):
        return False

    parsed = urlparse(url)
    # Must be same domain
    if parsed.netloc and base_domain not in parsed.netloc:
        return False
    # Must have a path
    if not parsed.path or parsed.path == "/":
        return False

    return True


def _extract_slug_from_url(url: str, marketplace_id: str) -> str:
    """Generate a slug from a subcategory URL for tracking."""
    parsed = urlparse(url)
    path = parsed.path.strip("/")

    # Use last 2-3 path segments as slug
    segments = [s for s in path.split("/") if s]
    if len(segments) >= 2:
        slug = "-".join(segments[-2:])
    elif segments:
        slug = segments[-1]
    else:
        slug = path.replace("/", "-")

    # Clean up
    slug = re.sub(r"[^a-z0-9-]", "", slug.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")

    return f"discovered-{slug}" if slug else ""


class SubcategoryDiscovery:
    """
    Discovers subcategories from category pages.

    Usage:
        discovery = SubcategoryDiscovery()
        subcats = discovery.extract_subcategories(html, marketplace_id, base_url)
        # Returns list of {"slug": ..., "url": ...} dicts
    """

    def __init__(self, max_depth: int = MAX_DISCOVERY_DEPTH):
        self._max_depth = max_depth
        self._discovered: dict[str, set[str]] = {}  # marketplace -> set of URLs already found

    def extract_subcategories(
        self,
        html: str,
        marketplace_id: str,
        base_url: str,
        current_depth: int = 0,
    ) -> list[dict[str, str]]:
        """
        Extract subcategory links from a category page.

        Args:
            html: Page HTML content.
            marketplace_id: Marketplace identifier.
            base_url: Current page URL (for resolving relative links).
            current_depth: Current recursion depth.

        Returns:
            List of {"slug": str, "url": str} dicts for discovered subcategories.
        """
        if current_depth >= self._max_depth:
            return []

        if marketplace_id not in self._discovered:
            self._discovered[marketplace_id] = set()

        soup = BeautifulSoup(html, "html.parser")
        selectors = _get_selectors(marketplace_id)
        base_domain = urlparse(base_url).netloc

        subcategories: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        for selector in selectors:
            links = soup.select(selector)
            for link in links:
                href = link.get("href", "")
                if not href:
                    continue

                # Resolve relative URLs
                full_url = urljoin(base_url, href)

                # Normalize — remove trailing slash, query params for dedup
                normalized = full_url.split("?")[0].rstrip("/")

                if normalized in seen_urls:
                    continue
                if normalized in self._discovered[marketplace_id]:
                    continue
                if not _is_valid_subcategory_url(full_url, base_domain):
                    continue
                # Don't add the base URL itself
                if normalized == base_url.split("?")[0].rstrip("/"):
                    continue

                seen_urls.add(normalized)
                self._discovered[marketplace_id].add(normalized)

                slug = _extract_slug_from_url(full_url, marketplace_id)
                if slug:
                    subcategories.append({
                        "slug": slug,
                        "url": full_url,
                        "depth": current_depth + 1,
                    })

        if subcategories:
            logger.info(
                "[subcategory] Discovered %d subcategories on %s from %s",
                len(subcategories), marketplace_id, base_url[:80],
            )

        return subcategories

    @property
    def total_discovered(self) -> int:
        """Total unique subcategories discovered across all marketplaces."""
        return sum(len(urls) for urls in self._discovered.values())

    def reset(self) -> None:
        """Clear discovered subcategories for a fresh cycle."""
        self._discovered.clear()
