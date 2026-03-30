"""
Arbitrage Validator — final anti-false-positive layer.

Sits AFTER the opportunity engine scores an opportunity and BEFORE
it is emitted as actionable.  Every check is independent and
non-short-circuiting so the full rejection audit trail is always
available.

Checks implemented:
  1. Price freshness — reject stale listings
  2. Spread stability — reject volatile markets (CV > threshold)
  3. Depth confirmation — reject thin/illiquid markets
  4. Worst-case profit — re-verify at P25 sell + slippage
  5. Execution simulation — buy→transfer→sell with fees and decay
  6. Duplicate suppression — hash-based dedup within a time window
  7. Confidence re-scoring — composite of all signals

All checks produce a single ValidationResult with full observability.
"""
import hashlib
import logging
import math
import os
import statistics
import time
from dataclasses import dataclass, field

from .currency import to_usd

SHADOW_MODE = os.getenv("SHADOW_MODE", "false").lower() in ("1", "true", "yes")
from .opportunity import (
    MIN_PROFIT_USD,
    calculate_profit,
)
from .routes_config import is_cross_border

logger = logging.getLogger(__name__)


# ── Configuration ────────────────────────────────────────────

@dataclass
class ValidatorConfig:
    """All thresholds in one place.  Override via constructor for tests."""

    # Step 1 — price freshness
    max_price_age_seconds: float = 300.0        # 5 minutes

    # Step 2 — spread stability
    max_cv: float = 0.25                        # coefficient of variation

    # Step 3 — depth confirmation
    min_compatible_listings: int = 2            # within 5% of sell price
    min_total_sales: int = 3                    # aggregate sales_count

    # Step 4 — worst-case profit
    sell_percentile: float = 0.25               # use P25 sell price
    currency_slippage_pct: float = 0.02         # 2% FX buffer
    fee_buffer_pct: float = 0.05                # 5% extra fee margin

    # Step 5 — execution simulation
    execution_delay_seconds: float = 180.0      # 3 min average
    price_decay_per_hour: float = 0.005         # 0.5%/hour decay

    # Step 6 — duplicate suppression
    dedup_window_seconds: float = 600.0         # 10 min window

    # Step 7 — confidence re-scoring
    min_execution_confidence: float = 0.30      # minimum composite


# ── Result types ─────────────────────────────────────────────

@dataclass(frozen=True)
class CheckDetail:
    """One validation check's outcome."""
    name: str
    passed: bool
    reason: str = ""
    value: float = 0.0


@dataclass
class ValidationResult:
    """Complete validation output — full audit trail."""
    is_valid: bool
    rejection_reasons: list[str]
    risk_score: float                           # 0-100
    execution_confidence: float                 # 0-1
    checks: list[CheckDetail] = field(default_factory=list)
    worst_case_profit: float = 0.0
    simulated_profit: float = 0.0


# ── Input snapshot ───────────────────────────────────────────

@dataclass
class ArbitrageSnapshot:
    """All data needed by the validator — decoupled from ORM."""

    # Core prices
    buy_price_usd: float
    sell_price_usd: float
    buy_marketplace: str
    sell_marketplace: str

    # Listing metadata
    buy_listing_scraped_at: float               # epoch seconds
    sell_listing_scraped_at: float               # epoch seconds
    buy_listing_id: int = 0
    sell_listing_id: int = 0
    product_id: int = 0

    # Sell-side market data
    compatible_sell_prices_usd: list[float] = field(default_factory=list)
    total_sales_count: int = 0

    # Shipping
    buy_free_shipping: bool = False
    sell_free_shipping: bool = False

    # Existing scores (from scoring layer)
    opportunity_score: float = 0.0
    risk_score: float = 50.0
    confidence_score: float = 50.0
    price_stability_score: float = 50.0
    liquidity_score: float = 50.0

    # Fees already computed
    total_fees: float = 0.0
    net_profit: float = 0.0
    roi: float = 0.0

    # Exchange rates for fee recalculation
    rates: dict[str, float] | None = None


# ── Duplicate tracker (module-level singleton) ───────────────

class _DedupTracker:
    """Time-windowed hash set for duplicate suppression."""

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}       # hash → timestamp

    def is_duplicate(
        self, key: str, now: float, window: float,
    ) -> bool:
        """Return True if key was seen within the last *window* seconds."""
        self._evict(now, window)
        if key in self._seen:
            return True
        self._seen[key] = now
        return False

    def _evict(self, now: float, window: float) -> None:
        cutoff = now - window
        expired = [k for k, ts in self._seen.items() if ts < cutoff]
        for k in expired:
            del self._seen[k]

    def clear(self) -> None:
        self._seen.clear()

    @property
    def size(self) -> int:
        return len(self._seen)


_dedup = _DedupTracker()


def reset_dedup() -> None:
    """Reset for testing."""
    _dedup.clear()


# ── Individual checks ────────────────────────────────────────

def check_price_freshness(
    snap: ArbitrageSnapshot, cfg: ValidatorConfig,
) -> CheckDetail:
    """Reject if either listing is older than max_price_age_seconds."""
    now = time.time()
    buy_age = now - snap.buy_listing_scraped_at
    sell_age = now - snap.sell_listing_scraped_at
    max_age = max(buy_age, sell_age)

    if max_age <= cfg.max_price_age_seconds:
        return CheckDetail("price_freshness", True, value=max_age)

    return CheckDetail(
        "price_freshness", False,
        reason=f"listing age {max_age:.0f}s > {cfg.max_price_age_seconds:.0f}s",
        value=max_age,
    )


def check_spread_stability(
    snap: ArbitrageSnapshot, cfg: ValidatorConfig,
) -> CheckDetail:
    """Reject if sell-side price CV exceeds threshold."""
    prices = snap.compatible_sell_prices_usd
    if len(prices) < 2:
        # Not enough data to measure stability — pass with caution
        return CheckDetail("spread_stability", True, value=0.0)

    avg = statistics.mean(prices)
    if avg <= 0:
        return CheckDetail("spread_stability", True, value=0.0)

    cv = statistics.stdev(prices) / avg

    if cv <= cfg.max_cv:
        return CheckDetail("spread_stability", True, value=round(cv, 4))

    return CheckDetail(
        "spread_stability", False,
        reason=f"price CV {cv:.3f} > {cfg.max_cv}",
        value=round(cv, 4),
    )


def check_depth(
    snap: ArbitrageSnapshot, cfg: ValidatorConfig,
) -> CheckDetail:
    """Reject if market is too thin (few compatible listings or low sales)."""
    sell_price = snap.sell_price_usd
    upper_bound = sell_price * 1.05
    nearby = sum(1 for p in snap.compatible_sell_prices_usd if p <= upper_bound)

    if nearby < cfg.min_compatible_listings:
        return CheckDetail(
            "depth", False,
            reason=(
                f"only {nearby} listings within 5% of sell price "
                f"(need {cfg.min_compatible_listings})"
            ),
            value=float(nearby),
        )

    if snap.total_sales_count < cfg.min_total_sales:
        return CheckDetail(
            "depth", False,
            reason=(
                f"total_sales {snap.total_sales_count} < {cfg.min_total_sales}"
            ),
            value=float(snap.total_sales_count),
        )

    return CheckDetail("depth", True, value=float(nearby))


def check_worst_case_profit(
    snap: ArbitrageSnapshot, cfg: ValidatorConfig,
) -> tuple[CheckDetail, float]:
    """Re-verify profit using P25 sell price, worst-case fees, and FX buffer.

    Returns (check, worst_case_profit_usd).
    """
    prices = sorted(snap.compatible_sell_prices_usd)

    if len(prices) >= 4:
        idx = max(0, int(len(prices) * cfg.sell_percentile) - 1)
        p25_sell = prices[idx]
    elif prices:
        p25_sell = prices[0]  # use worst observed
    else:
        p25_sell = snap.sell_price_usd

    # Apply FX slippage buffer to buy side
    adjusted_buy = snap.buy_price_usd * (1 + cfg.currency_slippage_pct)

    # Apply fee buffer
    adjusted_fees = snap.total_fees * (1 + cfg.fee_buffer_pct)

    worst_profit = p25_sell - adjusted_buy - adjusted_fees

    if worst_profit > 0:
        return (
            CheckDetail("worst_case_profit", True, value=round(worst_profit, 2)),
            worst_profit,
        )

    return (
        CheckDetail(
            "worst_case_profit", False,
            reason=f"worst-case profit ${worst_profit:.2f} <= $0 "
                   f"(P25_sell=${p25_sell:.2f}, adj_buy=${adjusted_buy:.2f}, "
                   f"adj_fees=${adjusted_fees:.2f})",
            value=round(worst_profit, 2),
        ),
        worst_profit,
    )


def check_execution_simulation(
    snap: ArbitrageSnapshot, cfg: ValidatorConfig,
) -> tuple[CheckDetail, float]:
    """Simulate buy→transfer→sell with time decay.

    Returns (check, simulated_profit).
    """
    delay_hours = cfg.execution_delay_seconds / 3600.0
    decay_factor = 1.0 - (cfg.price_decay_per_hour * delay_hours)
    decay_factor = max(0.0, decay_factor)

    decayed_sell = snap.sell_price_usd * decay_factor

    # Re-calculate profit with full fee path (including FX rates for fixed fees)
    calc = calculate_profit(
        snap.buy_price_usd, decayed_sell,
        snap.buy_marketplace, snap.sell_marketplace,
        snap.buy_free_shipping, snap.sell_free_shipping,
        rates=snap.rates,
    )

    if calc.net_profit > 0:
        return (
            CheckDetail(
                "execution_simulation", True,
                value=round(calc.net_profit, 2),
            ),
            calc.net_profit,
        )

    return (
        CheckDetail(
            "execution_simulation", False,
            reason=(
                f"simulated profit ${calc.net_profit:.2f} after "
                f"{delay_hours:.2f}h decay (factor={decay_factor:.4f})"
            ),
            value=round(calc.net_profit, 2),
        ),
        calc.net_profit,
    )


def check_duplicate(
    snap: ArbitrageSnapshot, cfg: ValidatorConfig,
) -> CheckDetail:
    """Suppress duplicate opportunities within a time window."""
    raw = f"{snap.buy_listing_id}|{snap.sell_marketplace}|{snap.product_id}"
    key = hashlib.sha256(raw.encode()).hexdigest()[:16]
    now = time.time()

    if _dedup.is_duplicate(key, now, cfg.dedup_window_seconds):
        return CheckDetail(
            "duplicate", False,
            reason=f"duplicate within {cfg.dedup_window_seconds:.0f}s window",
        )

    return CheckDetail("duplicate", True)


def compute_execution_confidence(
    snap: ArbitrageSnapshot,
    checks: list[CheckDetail],
) -> float:
    """Composite execution confidence from all signals.

    Combines:
    - Existing scores (opportunity, confidence, stability, liquidity)
    - Validation check pass rate
    - Inverse of risk score
    """
    # Normalize existing scores to 0-1
    opp = snap.opportunity_score / 100.0
    conf = snap.confidence_score / 100.0
    stab = snap.price_stability_score / 100.0
    liq = snap.liquidity_score / 100.0
    inv_risk = 1.0 - (snap.risk_score / 100.0)

    # Check pass rate
    total = len(checks) if checks else 1
    passed = sum(1 for c in checks if c.passed)
    pass_rate = passed / total

    # Weighted composite
    composite = (
        conf * 0.25
        + liq * 0.20
        + stab * 0.15
        + inv_risk * 0.15
        + opp * 0.10
        + pass_rate * 0.15
    )

    return round(max(0.0, min(1.0, composite)), 4)


# ── Main validator ───────────────────────────────────────────

def validate_arbitrage(
    snap: ArbitrageSnapshot,
    config: ValidatorConfig | None = None,
    *,
    shadow_mode: bool = False,
) -> ValidationResult:
    """Run ALL validation checks.  No short-circuit.

    Args:
        snap: The opportunity data to validate.
        config: Override thresholds (defaults are conservative).
        shadow_mode: If True, always returns is_valid=True but logs
                     what WOULD have been rejected.  Use for A/B
                     comparison before fully enabling.

    Returns:
        ValidationResult with full audit trail.
    """
    cfg = config or ValidatorConfig()
    checks: list[CheckDetail] = []
    rejection_reasons: list[str] = []

    # Step 1 — price freshness
    c1 = check_price_freshness(snap, cfg)
    checks.append(c1)
    if not c1.passed:
        rejection_reasons.append(c1.reason)

    # Step 2 — spread stability
    c2 = check_spread_stability(snap, cfg)
    checks.append(c2)
    if not c2.passed:
        rejection_reasons.append(c2.reason)

    # Step 3 — depth confirmation
    c3 = check_depth(snap, cfg)
    checks.append(c3)
    if not c3.passed:
        rejection_reasons.append(c3.reason)

    # Step 4 — worst-case profit
    c4, worst_profit = check_worst_case_profit(snap, cfg)
    checks.append(c4)
    if not c4.passed:
        rejection_reasons.append(c4.reason)

    # Step 5 — execution simulation
    c5, sim_profit = check_execution_simulation(snap, cfg)
    checks.append(c5)
    if not c5.passed:
        rejection_reasons.append(c5.reason)

    # Step 6 — duplicate suppression
    c6 = check_duplicate(snap, cfg)
    checks.append(c6)
    if not c6.passed:
        rejection_reasons.append(c6.reason)

    # Step 7 — confidence re-scoring
    exec_confidence = compute_execution_confidence(snap, checks)
    confidence_check = CheckDetail(
        "execution_confidence",
        exec_confidence >= cfg.min_execution_confidence,
        reason=(
            f"execution_confidence {exec_confidence:.3f} < "
            f"{cfg.min_execution_confidence}"
            if exec_confidence < cfg.min_execution_confidence
            else ""
        ),
        value=exec_confidence,
    )
    checks.append(confidence_check)
    if not confidence_check.passed:
        rejection_reasons.append(confidence_check.reason)

    is_valid = len(rejection_reasons) == 0

    # Shadow mode: log but don't reject
    if shadow_mode and not is_valid:
        logger.info(
            "[shadow] would reject product=%d %s→%s (%d reasons): %s",
            snap.product_id, snap.buy_marketplace, snap.sell_marketplace,
            len(rejection_reasons), "; ".join(rejection_reasons),
        )
        is_valid = True

    if not is_valid:
        logger.debug(
            "arbitrage rejected product=%d (%d reasons): %s",
            snap.product_id, len(rejection_reasons),
            "; ".join(rejection_reasons),
        )

    return ValidationResult(
        is_valid=is_valid,
        rejection_reasons=rejection_reasons,
        risk_score=snap.risk_score,
        execution_confidence=exec_confidence,
        checks=checks,
        worst_case_profit=round(worst_profit, 2),
        simulated_profit=round(sim_profit, 2),
    )
