"""
Price anomaly detection for incoming listings.

Compares a new price against historical prices for the same URL.
Rejects prices that deviate too far from the historical median,
which prevents bad parses ($0, $1, $999999) from contaminating the dataset.
"""
import logging
import statistics

logger = logging.getLogger(__name__)

# A price is anomalous if it's more than MAX_RATIO times the median
# or less than MIN_RATIO times the median.
MAX_RATIO = 5.0
MIN_RATIO = 0.2

# Currency-specific absolute bounds
CURRENCY_BOUNDS: dict[str, tuple[float, float]] = {
    "ARS": (500, 50_000_000),
    "MXN": (10, 500_000),
    "USD": (0.50, 50_000),
    "EUR": (0.50, 50_000),
    "GBP": (0.50, 50_000),
    "BRL": (2.0, 300_000),
}

# Fallback bounds for unknown currencies
_DEFAULT_BOUNDS = (1.0, 500_000)

# Minimum historical data points required to apply median-based detection
MIN_HISTORY_SIZE = 3


def detect_price_anomaly(
    price: float,
    historical_prices: list[float],
    currency: str = "USD",
) -> bool:
    """Return True if the price is anomalous and should NOT be saved.

    Logic:
    1. Reject prices outside currency-specific absolute bounds.
    2. If enough historical data exists, reject prices that deviate
       more than MAX_RATIO (5x) or less than MIN_RATIO (0.2x) from the median.
    3. If no history exists, only absolute bounds apply.
    """
    min_p, max_p = CURRENCY_BOUNDS.get(currency, _DEFAULT_BOUNDS)

    if price <= min_p or price >= max_p:
        logger.warning("Price anomaly (absolute bounds): %.2f %s (bounds: %.2f-%.2f)", price, currency, min_p, max_p)
        return True

    # Not enough history to compare — accept the price
    if len(historical_prices) < MIN_HISTORY_SIZE:
        return False

    median = statistics.median(historical_prices)
    if median <= 0:
        return False

    ratio = price / median
    if ratio > MAX_RATIO or ratio < MIN_RATIO:
        logger.warning(
            "Price anomaly (median deviation): %.2f %s vs median %.2f (ratio=%.2fx)",
            price, currency, median, ratio,
        )
        return True

    return False
