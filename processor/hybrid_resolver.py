"""
Hybrid Product Resolver — Chain of Responsibility pattern.

Solves the core problem: embedding similarity merges different models
(e.g., Galaxy A11 vs A16) because their titles are semantically similar.

Architecture:
  Each listing passes through a chain of matchers. Each matcher can:
    - ACCEPT: definitively match to a master product (high confidence)
    - REJECT: definitively not a match (hard rule violation)
    - PASS: insufficient signal, delegate to next matcher

  Chain order (most specific → most general):
    1. ExactModelMatcher   → exact brand+model string match
    2. StructuredAttrMatcher → storage, condition, variant compatibility
    3. SemanticMatcher      → embedding cosine similarity (existing)
    4. ConfidenceAggregator → final confidence score

Design patterns:
  - Chain of Responsibility: matchers are chained, each can handle or pass
  - Strategy: different matching strategies per chain link
  - Composite: final confidence aggregates all matcher signals
"""
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class MatchVerdict(Enum):
    ACCEPT = "accept"   # Definitive match
    REJECT = "reject"   # Definitive non-match
    PASS = "pass"       # Insufficient signal, check next


@dataclass
class MatchSignal:
    """Signal emitted by a single matcher in the chain."""
    verdict: MatchVerdict
    confidence: float = 0.0       # 0.0 - 1.0
    reason: str = ""
    matcher_name: str = ""


@dataclass
class MatchCandidate:
    """A potential match between a listing and a master product."""
    master_product_id: int
    canonical_name: str
    brand: str | None = None
    model: str | None = None
    category: str | None = None
    embedding_similarity: float = 0.0
    signals: list[MatchSignal] = field(default_factory=list)

    @property
    def composite_confidence(self) -> float:
        """Aggregate confidence from all signals."""
        if not self.signals:
            return 0.0
        # Weighted: ACCEPT signals boost, REJECT kills, PASS is neutral
        accept_conf = [s.confidence for s in self.signals if s.verdict == MatchVerdict.ACCEPT]
        reject_count = sum(1 for s in self.signals if s.verdict == MatchVerdict.REJECT)

        if reject_count > 0:
            return 0.0  # Any rejection = no match

        if not accept_conf:
            return self.embedding_similarity * 0.5  # Only semantic, halved confidence

        return sum(accept_conf) / len(accept_conf)


@dataclass
class ListingInput:
    """Normalized listing data for matching."""
    title: str
    normalized_title: str
    brand: str | None
    model: str | None
    category: str | None
    price_usd: float
    currency: str
    marketplace_id: str
    condition: str | None = None
    embedding: list[float] | None = None


# ── Abstract Matcher (Chain of Responsibility) ───────────────

class ProductMatcher(ABC):
    """Base class for chain-of-responsibility matchers."""

    def __init__(self) -> None:
        self._next: ProductMatcher | None = None

    def set_next(self, matcher: "ProductMatcher") -> "ProductMatcher":
        self._next = matcher
        return matcher

    def evaluate(self, listing: ListingInput, candidate: MatchCandidate) -> MatchSignal:
        """Evaluate this matcher, then pass to next if PASS."""
        signal = self._match(listing, candidate)
        candidate.signals.append(signal)

        if signal.verdict == MatchVerdict.REJECT:
            return signal  # Stop chain on rejection

        if signal.verdict == MatchVerdict.PASS and self._next:
            return self._next.evaluate(listing, candidate)

        if self._next and signal.verdict == MatchVerdict.ACCEPT:
            # Continue chain to accumulate confidence signals
            self._next.evaluate(listing, candidate)

        return signal

    @abstractmethod
    def _match(self, listing: ListingInput, candidate: MatchCandidate) -> MatchSignal:
        """Implement matching logic. Return MatchSignal."""
        ...


# ── Concrete Matchers ────────────────────────────────────────

class BrandMatcher(ProductMatcher):
    """
    Step 1: Brand consistency check.
    If both have brands and they differ → REJECT.
    If both have same brand → ACCEPT with moderate confidence.
    If either is missing → PASS.
    """

    def _match(self, listing: ListingInput, candidate: MatchCandidate) -> MatchSignal:
        l_brand = (listing.brand or "").lower().strip()
        c_brand = (candidate.brand or "").lower().strip()

        if not l_brand or not c_brand:
            return MatchSignal(MatchVerdict.PASS, 0.0, "brand unknown", "BrandMatcher")

        if l_brand == c_brand:
            return MatchSignal(MatchVerdict.ACCEPT, 0.6, f"brand match: {l_brand}", "BrandMatcher")

        # Handle brand aliases
        aliases = {
            "ml": "mercadolibre", "apple": "apple", "samsung": "samsung",
            "lg": "lg electronics", "hp": "hewlett-packard",
        }
        l_norm = aliases.get(l_brand, l_brand)
        c_norm = aliases.get(c_brand, c_brand)

        if l_norm == c_norm:
            return MatchSignal(MatchVerdict.ACCEPT, 0.55, f"brand alias match: {l_brand}={c_brand}", "BrandMatcher")

        return MatchSignal(MatchVerdict.REJECT, 0.0, f"brand mismatch: {l_brand} ≠ {c_brand}", "BrandMatcher")


class ModelMatcher(ProductMatcher):
    """
    Step 2: Model number extraction and comparison.
    This is the KEY fix for the A11 vs A16 problem.

    Extracts structured model identifiers and compares them exactly.
    Different model numbers = REJECT even if titles are semantically similar.
    """

    # Model number patterns — ordered by specificity
    _PATTERNS = [
        # Phone models: iPhone 15 Pro Max, Galaxy S25 Ultra, Pixel 9 Pro
        re.compile(r'(iphone\s*\d+\s*(?:pro\s*max|pro|plus|e|mini)?)', re.I),
        re.compile(r'(galaxy\s*(?:s|a|z|tab|watch|buds)\s*\d+\s*(?:ultra|plus|fe|pro|lite|fold|flip)?(?:\s*\d+)?)', re.I),
        re.compile(r'(pixel\s*\d+\s*(?:pro\s*xl|pro\s*fold|pro|a)?)', re.I),
        re.compile(r'(redmi\s*(?:note\s*)?\d+\s*(?:pro\s*max|pro\s*plus|pro|s|c|a)?)', re.I),
        # Headphones: WH-1000XM5, AirPods Pro 2
        re.compile(r'([a-z]{2,3}[\-]?\d{3,4}[a-z]*\d*)', re.I),
        re.compile(r'(airpods\s*(?:pro|max)?\s*\d*)', re.I),
        # RAM: DDR5 6000 32GB
        re.compile(r'(ddr\d\s*\d+(?:mhz)?\s*\d+\s*gb)', re.I),
        # Storage capacity as model identifier
        re.compile(r'(\d+\s*(?:gb|tb))\b', re.I),
        # Generic alphanumeric model: CL34, KF568
        re.compile(r'\b([a-z]{1,3}\d{2,4}[a-z]?\d*)\b', re.I),
    ]

    def _extract_models(self, text: str) -> set[str]:
        """Extract all model identifiers from text."""
        models = set()
        text_lower = text.lower()
        for pat in self._PATTERNS:
            for m in pat.finditer(text_lower):
                normalized = re.sub(r'\s+', '', m.group(1).strip())
                if len(normalized) >= 2:
                    models.add(normalized)
        return models

    def _match(self, listing: ListingInput, candidate: MatchCandidate) -> MatchSignal:
        l_models = self._extract_models(listing.normalized_title)
        c_models = self._extract_models(candidate.canonical_name)

        if not l_models or not c_models:
            return MatchSignal(MatchVerdict.PASS, 0.0, "no model IDs found", "ModelMatcher")

        overlap = l_models & c_models

        if overlap:
            conf = min(0.9, 0.5 + 0.1 * len(overlap))
            return MatchSignal(
                MatchVerdict.ACCEPT, conf,
                f"model match: {overlap}", "ModelMatcher",
            )

        # Both have model IDs but no overlap → different products
        return MatchSignal(
            MatchVerdict.REJECT, 0.0,
            f"model mismatch: {l_models} ∩ {c_models} = ∅", "ModelMatcher",
        )


class AttributeMatcher(ProductMatcher):
    """
    Step 3: Structured attribute comparison.
    Checks condition, storage, color variants, bundles.
    """

    _STORAGE = ("64gb", "128gb", "256gb", "512gb", "1tb", "2tb")
    _CONDITIONS = ("new", "used", "refurbished", "renewed", "reacondicionado")
    _BUNDLES = ("bundle", "combo", "kit", "pack", "set")

    def _extract_storage(self, text: str) -> str | None:
        t = text.lower().replace(" gb", "gb").replace(" tb", "tb")
        for s in self._STORAGE:
            if s in t:
                return s
        return None

    def _is_bundle(self, text: str) -> bool:
        t = text.lower()
        return any(kw in t for kw in self._BUNDLES)

    def _match(self, listing: ListingInput, candidate: MatchCandidate) -> MatchSignal:
        l_title = listing.normalized_title.lower()
        c_title = candidate.canonical_name.lower()

        # Storage mismatch
        l_storage = self._extract_storage(l_title)
        c_storage = self._extract_storage(c_title)
        if l_storage and c_storage and l_storage != c_storage:
            return MatchSignal(
                MatchVerdict.REJECT, 0.0,
                f"storage mismatch: {l_storage} ≠ {c_storage}", "AttributeMatcher",
            )

        # Bundle mismatch
        l_bundle = self._is_bundle(l_title)
        c_bundle = self._is_bundle(c_title)
        if l_bundle != c_bundle:
            return MatchSignal(
                MatchVerdict.REJECT, 0.0,
                "bundle mismatch", "AttributeMatcher",
            )

        # If storage matches, boost confidence
        if l_storage and c_storage and l_storage == c_storage:
            return MatchSignal(
                MatchVerdict.ACCEPT, 0.7,
                f"storage match: {l_storage}", "AttributeMatcher",
            )

        return MatchSignal(MatchVerdict.PASS, 0.0, "no structured attrs to compare", "AttributeMatcher")


class SemanticMatcher(ProductMatcher):
    """
    Step 4: Embedding cosine similarity (existing system).
    Only used as final fallback when structured matchers PASS.
    """

    def __init__(self, threshold: float = 0.82) -> None:
        super().__init__()
        self.threshold = threshold

    def _match(self, listing: ListingInput, candidate: MatchCandidate) -> MatchSignal:
        sim = candidate.embedding_similarity

        if sim >= self.threshold:
            # Scale confidence by how much it exceeds threshold
            excess = (sim - self.threshold) / (1.0 - self.threshold)
            conf = 0.5 + 0.4 * excess  # 0.5 to 0.9
            return MatchSignal(
                MatchVerdict.ACCEPT, conf,
                f"semantic sim={sim:.3f} ≥ {self.threshold}", "SemanticMatcher",
            )

        if sim >= self.threshold * 0.85:
            return MatchSignal(
                MatchVerdict.PASS, 0.0,
                f"semantic sim={sim:.3f} borderline", "SemanticMatcher",
            )

        return MatchSignal(
            MatchVerdict.REJECT, 0.0,
            f"semantic sim={sim:.3f} < {self.threshold}", "SemanticMatcher",
        )


# ── Resolver Chain Factory ────────────────────────────────────

def build_resolver_chain(semantic_threshold: float = 0.82) -> ProductMatcher:
    """
    Build the default resolver chain.

    Order:
      BrandMatcher → ModelMatcher → AttributeMatcher → SemanticMatcher

    A REJECT at any stage stops the chain.
    ACCEPTs accumulate and contribute to composite confidence.
    """
    brand = BrandMatcher()
    model = ModelMatcher()
    attr = AttributeMatcher()
    semantic = SemanticMatcher(threshold=semantic_threshold)

    brand.set_next(model)
    model.set_next(attr)
    attr.set_next(semantic)

    return brand


# ── Hybrid Resolve Function ──────────────────────────────────

def hybrid_match(
    listing: ListingInput,
    candidates: list[MatchCandidate],
    min_confidence: float = 0.45,
    strict: bool = False,
) -> MatchCandidate | None:
    """
    Run all candidates through the hybrid resolver chain.
    Returns the best match above min_confidence, or None.

    If strict=True, delegates to strict_hybrid_match for high-precision mode.
    """
    if strict:
        return strict_hybrid_match(listing, candidates)

    chain = build_resolver_chain()
    best: MatchCandidate | None = None
    best_conf = 0.0

    for candidate in candidates:
        # Reset signals for fresh evaluation
        candidate.signals = []
        chain.evaluate(listing, candidate)
        conf = candidate.composite_confidence

        if conf > best_conf and conf >= min_confidence:
            best = candidate
            best_conf = conf

    if best:
        logger.debug(
            "hybrid_match: %s → %s (conf=%.2f, signals=%d)",
            listing.normalized_title[:50],
            best.canonical_name[:50],
            best_conf,
            len(best.signals),
        )

    return best


# ══════════════════════════════════════════════════════════════
# Strict Match Mode (hardened pipeline)
# ══════════════════════════════════════════════════════════════


@dataclass
class StrictMatchConfig:
    """Configuration for high-precision matching."""
    require_brand_exact: bool = True
    require_model_exact: bool = True
    reject_accessories: bool = True
    semantic_max_weight: float = 0.20
    min_confidence: float = 0.70


REJECT_TITLE_KEYWORDS: tuple[str, ...] = (
    # Bundles & combos
    "bundle", "kit", "pack", "combo", "set", "lote",
    # Compatibility markers
    "compatible", "for ", "para ",
    # Accessories
    "funda", "case", "cover", "cable", "cargador", "charger",
    "protector", "strap", "correa", "band", "mount", "soporte",
    "adapter", "adaptador", "holder", "bracket", "film", "mica",
    "glass", "vidrio", "skin", "pouch", "estuche", "bolsa",
    "replacement", "repuesto", "reemplazo", "tip", "punta",
)


class StrictTitleFilter(ProductMatcher):
    """Front-of-chain filter that rejects listings with accessory/bundle keywords."""

    def _match(self, listing: ListingInput, candidate: MatchCandidate) -> MatchSignal:
        title_lower = listing.normalized_title.lower()
        for kw in REJECT_TITLE_KEYWORDS:
            if kw in title_lower:
                return MatchSignal(
                    MatchVerdict.REJECT, 0.0,
                    f"title contains reject keyword: '{kw}'",
                    "StrictTitleFilter",
                )
        return MatchSignal(MatchVerdict.PASS, 0.0, "title clean", "StrictTitleFilter")


def build_strict_resolver_chain(semantic_threshold: float = 0.88) -> ProductMatcher:
    """Build a strict chain: TitleFilter → Brand → Model → Attribute → Semantic(0.88)."""
    title_filter = StrictTitleFilter()
    brand = BrandMatcher()
    model = ModelMatcher()
    attr = AttributeMatcher()
    semantic = SemanticMatcher(threshold=semantic_threshold)

    title_filter.set_next(brand)
    brand.set_next(model)
    model.set_next(attr)
    attr.set_next(semantic)

    return title_filter


def strict_hybrid_match(
    listing: ListingInput,
    candidates: list[MatchCandidate],
    config: StrictMatchConfig | None = None,
) -> MatchCandidate | None:
    """High-precision matching with post-validation.

    Post-validates:
    - Requires ACCEPT from both Brand AND Model (not just one)
    - Caps semantic-only confidence at semantic_max_weight
    - Applies min_confidence threshold
    - Logs structured DEBUG trace for every decision
    """
    cfg = config or StrictMatchConfig()
    chain = build_strict_resolver_chain()
    best: MatchCandidate | None = None
    best_conf = 0.0

    for candidate in candidates:
        candidate.signals = []
        chain.evaluate(listing, candidate)

        # Post-validation: require both brand and model ACCEPT
        signal_map = {s.matcher_name: s for s in candidate.signals}

        brand_signal = signal_map.get("BrandMatcher")
        model_signal = signal_map.get("ModelMatcher")

        brand_accepted = brand_signal and brand_signal.verdict == MatchVerdict.ACCEPT
        model_accepted = model_signal and model_signal.verdict == MatchVerdict.ACCEPT

        if cfg.require_brand_exact and not brand_accepted:
            logger.debug(
                "[strict] REJECT %s → %s: brand not confirmed (%s)",
                listing.normalized_title[:40],
                candidate.canonical_name[:40],
                brand_signal.reason if brand_signal else "missing",
            )
            continue

        if cfg.require_model_exact and not model_accepted:
            logger.debug(
                "[strict] REJECT %s → %s: model not confirmed (%s)",
                listing.normalized_title[:40],
                candidate.canonical_name[:40],
                model_signal.reason if model_signal else "missing",
            )
            continue

        # Cap semantic-only confidence
        conf = candidate.composite_confidence
        has_structural_accept = brand_accepted or model_accepted
        semantic_signal = signal_map.get("SemanticMatcher")
        semantic_only = (
            not has_structural_accept
            and semantic_signal
            and semantic_signal.verdict == MatchVerdict.ACCEPT
        )
        if semantic_only:
            conf = min(conf, cfg.semantic_max_weight)

        if conf < cfg.min_confidence:
            logger.debug(
                "[strict] REJECT %s → %s: confidence %.2f < %.2f",
                listing.normalized_title[:40],
                candidate.canonical_name[:40],
                conf, cfg.min_confidence,
            )
            continue

        logger.debug(
            "[strict] ACCEPT %s → %s (conf=%.2f, brand=%s, model=%s)",
            listing.normalized_title[:40],
            candidate.canonical_name[:40],
            conf,
            "Y" if brand_accepted else "N",
            "Y" if model_accepted else "N",
        )

        if conf > best_conf:
            best = candidate
            best_conf = conf

    return best
