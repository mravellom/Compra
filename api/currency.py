"""
Currency conversion with TTL cache.
Fetches once per hour, falls back to hardcoded rates.
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


async def get_rates() -> dict[str, float]:
    """Return cached rates, refreshing if TTL expired."""
    global _rates, _last_fetch

    if _rates and (time.monotonic() - _last_fetch) < _TTL_SECONDS:
        return _rates

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://open.er-api.com/v6/latest/USD")
            resp.raise_for_status()
            data = resp.json()
            _rates = data["rates"]
            _rates["USD"] = 1.0
            _last_fetch = time.monotonic()
            logger.info(
                "FX refreshed: ARS=%.1f MXN=%.2f EUR=%.2f",
                _rates.get("ARS", 0), _rates.get("MXN", 0), _rates.get("EUR", 0),
            )
    except Exception:
        logger.warning("FX fetch failed, using fallback rates", exc_info=True)
        if not _rates:
            _rates = FALLBACK_RATES.copy()
            _last_fetch = time.monotonic()

    return _rates


def to_usd(price: float, currency: str, rates: dict[str, float]) -> float:
    """Convert any currency to USD using provided rates."""
    if currency == "USD":
        return price
    rate = rates.get(currency, 1.0)
    return price / rate if rate > 0 else price


def convert(price: float, from_currency: str, to_currency: str, rates: dict[str, float]) -> float:
    """Convert between any two currencies."""
    usd = to_usd(price, from_currency, rates)
    if to_currency == "USD":
        return usd
    target_rate = rates.get(to_currency, 1.0)
    return usd * target_rate
