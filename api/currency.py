"""
Currency conversion with TTL cache.
Fetches once per hour from multiple sources, falls back to hardcoded rates.
"""
import logging
import time

import httpx

logger = logging.getLogger(__name__)

_rates: dict[str, float] = {}
_last_fetch: float = 0
_TTL_SECONDS = 3600  # 1 hour

FALLBACK_RATES: dict[str, float] = {
    "USD": 1.0,
    "ARS": 1450.0,
    "MXN": 17.8,
    "EUR": 0.86,
    "GBP": 0.75,
    "BRL": 5.10,
    "CLP": 950.0,
    "COP": 4100.0,
}

# Multiple sources for resilience
_FX_SOURCES = [
    "https://open.er-api.com/v6/latest/USD",
    "https://api.exchangerate-api.com/v4/latest/USD",
]

# Maximum deviation from fallback before we flag a rate as suspicious
_MAX_DEVIATION = 0.50  # 50%


def _validate_rates(rates: dict[str, float]) -> dict[str, float]:
    """Sanitize fetched rates: replace any that deviate >50% from reference with the reference value.

    Returns a cleaned copy. Individual corrupt rates are replaced with
    the best available reference (cached first, then fallback).
    """
    reference = _rates if _rates else FALLBACK_RATES
    cleaned = dict(rates)
    for currency, ref_val in reference.items():
        if currency == "USD" or currency not in cleaned:
            continue
        if ref_val <= 0:
            continue
        deviation = abs(cleaned[currency] - ref_val) / ref_val
        if deviation > _MAX_DEVIATION:
            logger.error(
                "FX ANOMALY REJECTED for %s: fetched=%.2f vs reference=%.2f (deviation=%.0f%%). Using reference.",
                currency, cleaned[currency], ref_val, deviation * 100,
            )
            cleaned[currency] = ref_val
    return cleaned


async def get_rates() -> dict[str, float]:
    """Return cached rates, refreshing if TTL expired."""
    global _rates, _last_fetch

    if _rates and (time.monotonic() - _last_fetch) < _TTL_SECONDS:
        return _rates

    for source_url in _FX_SOURCES:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(source_url)
                resp.raise_for_status()
                data = resp.json()
                fetched_rates = data.get("rates", data)
                fetched_rates["USD"] = 1.0
                _rates = _validate_rates(fetched_rates)
                _last_fetch = time.monotonic()
                logger.info(
                    "FX refreshed from %s: ARS=%.1f MXN=%.2f EUR=%.2f",
                    source_url,
                    _rates.get("ARS", 0), _rates.get("MXN", 0), _rates.get("EUR", 0),
                )
                return _rates
        except Exception:
            logger.warning("FX fetch failed from %s", source_url, exc_info=True)
            continue

    # All sources failed
    if not _rates:
        logger.warning("All FX sources failed, using fallback rates")
        _rates = FALLBACK_RATES.copy()
        _last_fetch = time.monotonic()

    return _rates


def fx_convert(
    amount: float,
    from_currency: str,
    to_currency: str,
    rates: dict[str, float],
) -> float:
    """Single entry point for all currency conversions.

    Converts *amount* from *from_currency* to *to_currency* via USD as
    the intermediate unit.  All code in the project should call this
    function instead of doing manual ``price / rate`` arithmetic.
    """
    if from_currency == to_currency:
        return amount
    # Step 1: from_currency → USD
    if from_currency == "USD":
        usd = amount
    else:
        rate = rates.get(from_currency, 1.0)
        usd = amount / rate if rate > 0 else amount
    # Step 2: USD → to_currency
    if to_currency == "USD":
        return usd
    target_rate = rates.get(to_currency, 1.0)
    return usd * target_rate if target_rate > 0 else usd


def to_usd(price: float, currency: str, rates: dict[str, float]) -> float:
    """Convert any currency to USD. Delegates to fx_convert."""
    return fx_convert(price, currency, "USD", rates)


def convert(price: float, from_currency: str, to_currency: str, rates: dict[str, float]) -> float:
    """Convert between any two currencies. Delegates to fx_convert."""
    return fx_convert(price, from_currency, to_currency, rates)
