"""
Robust multi-marketplace price normalization.

Handles the diverse price formats across MercadoLibre, Amazon, and eBay,
including split HTML elements, locale-specific separators, and edge cases.

Usage:
    from .price_parser import normalize_price, extract_price_from_element

    # From raw text
    price = normalize_price("$1,299.99", marketplace="amazon")

    # From BeautifulSoup element (handles split-element prices)
    price = extract_price_from_element(item, marketplace="mercadolibre")
"""
import logging
import re

from bs4 import Tag

logger = logging.getLogger(__name__)

# ── Marketplace price format rules ────────────────────────────
#
# MercadoLibre AR: "405.188" or "405188" (dot = thousands, no decimals in search)
#                  Cents shown separately in .andes-money-amount__cents
# MercadoLibre MX: "5,299" or "12,399" (comma = thousands, no decimals in search)
#                  Cents shown separately in .andes-money-amount__cents
# Amazon MX:       "$5,199.00" (comma = thousands, dot = decimal) via .a-offscreen
# eBay:            "$29.99" or "US $29.99" or "$10.00 to $20.00"

# Currency symbols to strip
_CURRENCY_SYMBOLS = re.compile(
    r"(?:US\s*)?[\$€£¥₹₽R\$]|MXN|ARS|USD|EUR|GBP|BRL",
    re.IGNORECASE,
)

# Whitespace including non-breaking spaces
_WHITESPACE = re.compile(r"[\s\u00a0\u200b]+")


def normalize_price(
    raw_price: str,
    marketplace: str = "",
    currency: str = "",
) -> float | None:
    """
    Parse a raw price string into a float.

    Handles:
    - Currency symbols removal
    - Whitespace/newline collapsing (split HTML elements)
    - Locale-specific thousands/decimal separators
    - Range prices ("$10.00 to $20.00" → takes first price)

    Returns None if parsing fails.
    """
    if not raw_price or not raw_price.strip():
        return None

    text = raw_price.strip()

    # Handle range prices BEFORE collapsing whitespace
    for sep in (" to ", " a ", " - ", "–", "—"):
        if sep in text:
            text = text.split(sep)[0].strip()
            break

    # Collapse whitespace (handles "193\n,\n70" → "193,70")
    text = _WHITESPACE.sub("", text)

    # Strip currency symbols
    text = _CURRENCY_SYMBOLS.sub("", text).strip()

    if not text:
        logger.debug("Price empty after cleanup: '%s'", raw_price)
        return None

    # Route to marketplace-specific parser
    mp = marketplace.lower()
    if "mercadolibre" in mp:
        return _parse_mercadolibre(text, currency)
    elif "amazon" in mp:
        return _parse_amazon(text)
    elif "ebay" in mp:
        return _parse_ebay(text)
    else:
        return _parse_generic(text)


def _parse_mercadolibre(text: str, currency: str = "") -> float | None:
    """
    MercadoLibre price parsing.

    AR format: "405.188" → 405188 (dot = thousands separator)
    MX format: "5,299" → 5299 (comma = thousands separator)
    With cents: "193,70" or "193.70" → depends on locale

    Rule: ML search results show integer prices. The fraction element has the
    integer part, and cents are in a separate element. If we get a value that
    looks like it has decimals (exactly 2 digits after separator), handle it.
    """
    # Remove any remaining non-numeric chars except dots and commas
    cleaned = re.sub(r"[^\d.,]", "", text)
    if not cleaned:
        return None

    return _parse_ambiguous_number(cleaned, currency)


def _parse_amazon(text: str) -> float | None:
    """
    Amazon MX price parsing.

    Amazon .a-offscreen gives clean prices like "$5,199.00".
    Format is always: comma = thousands, dot = decimal (US/MX locale).
    """
    cleaned = re.sub(r"[^\d.,]", "", text)
    if not cleaned:
        return None

    # Amazon always uses dot as decimal: remove commas (thousands), parse
    cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        logger.warning("Amazon price parse failed: '%s'", text)
        return None


def _parse_ebay(text: str) -> float | None:
    """
    eBay price parsing.

    Format: US locale — comma = thousands, dot = decimal.
    May have "US $" prefix (already stripped).
    """
    cleaned = re.sub(r"[^\d.,]", "", text)
    if not cleaned:
        return None

    cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        logger.warning("eBay price parse failed: '%s'", text)
        return None


def _parse_generic(text: str) -> float | None:
    """Fallback parser for unknown marketplaces."""
    cleaned = re.sub(r"[^\d.,]", "", text)
    if not cleaned:
        return None
    return _parse_ambiguous_number(cleaned)


def _parse_ambiguous_number(text: str, currency: str = "") -> float | None:
    """
    Parse a number string where the separator convention is unknown.

    Disambiguation rules:
    1. Only digits → integer
    2. Ends with separator + 2 digits → decimal (e.g., "193,70" or "1299.99")
    3. Ends with separator + 3 digits → thousands (e.g., "405.188" or "1,299")
    4. Multiple separators of same type → thousands (e.g., "1.000.000" or "1,000,000")
    5. Both dot and comma present → last one is decimal if 2 digits follow
    """
    if not text:
        return None

    dots = text.count(".")
    commas = text.count(",")

    # Case: only digits
    if dots == 0 and commas == 0:
        try:
            return float(text)
        except ValueError:
            return None

    # Case: both dot and comma present
    if dots > 0 and commas > 0:
        # Last separator determines decimal
        last_dot = text.rfind(".")
        last_comma = text.rfind(",")

        if last_dot > last_comma:
            # e.g., "1,299.99" → dot is decimal
            return _to_float(text.replace(",", ""))
        else:
            # e.g., "1.299,99" → comma is decimal
            return _to_float(text.replace(".", "").replace(",", "."))

    # Case: only dots
    if dots > 0 and commas == 0:
        if dots > 1:
            # Multiple dots = thousands separator: "1.000.000" → 1000000
            return _to_float(text.replace(".", ""))

        # Single dot: check digits after
        after_dot = text.split(".")[-1]
        if len(after_dot) == 3:
            # "405.188" → 405188 (thousands separator, common in ML AR)
            return _to_float(text.replace(".", ""))
        elif len(after_dot) <= 2:
            # "29.99" → 29.99 (decimal)
            return _to_float(text)
        else:
            # "1234.5678" → ambiguous, treat as decimal
            return _to_float(text)

    # Case: only commas
    if commas > 0 and dots == 0:
        if commas > 1:
            # Multiple commas = thousands: "1,000,000" → 1000000
            return _to_float(text.replace(",", ""))

        # Single comma: check digits after
        after_comma = text.split(",")[-1]
        if len(after_comma) == 3:
            # "5,299" → 5299 (thousands separator, common in ML MX)
            return _to_float(text.replace(",", ""))
        elif len(after_comma) <= 2:
            # "193,70" → 193.70 (decimal, common in AR/EU)
            return _to_float(text.replace(",", "."))
        else:
            # Treat as thousands
            return _to_float(text.replace(",", ""))

    return None


def _to_float(text: str) -> float | None:
    try:
        val = float(text)
        return val if val >= 0 else None
    except ValueError:
        return None


# ── HTML element extraction ───────────────────────────────────

def extract_ml_price(item: Tag) -> float | None:
    """
    Extract price from a MercadoLibre search result item.

    Handles the split-element structure:
    - .andes-money-amount__fraction → integer part
    - .andes-money-amount__cents → cents (optional)
    """
    fraction_el = item.select_one(".andes-money-amount__fraction")
    if not fraction_el:
        return None

    fraction_text = fraction_el.get_text(strip=True)
    if not fraction_text:
        return None

    # Clean the integer part (remove thousands separators)
    integer_part = re.sub(r"[^\d]", "", fraction_text)
    if not integer_part:
        return None

    # Check for cents element
    cents_el = item.select_one(".andes-money-amount__cents")
    cents = 0
    if cents_el:
        cents_text = re.sub(r"[^\d]", "", cents_el.get_text(strip=True))
        if cents_text:
            # Pad or truncate to 2 digits
            cents_text = cents_text[:2].ljust(2, "0")
            cents = int(cents_text)

    try:
        return float(f"{integer_part}.{cents:02d}")
    except ValueError:
        return None


def extract_amazon_price(item: Tag) -> float | None:
    """
    Extract price from an Amazon search result item.

    Primary: .a-price .a-offscreen (clean "$5,199.00")
    Fallback: .a-price-whole + .a-price-fraction (split elements)
    """
    # Primary: offscreen element has the full clean price
    offscreen = item.select_one(".a-price .a-offscreen")
    if offscreen:
        text = offscreen.get_text(strip=True)
        price = normalize_price(text, marketplace="amazon")
        if price is not None:
            return price

    # Fallback: split elements
    whole_el = item.select_one(".a-price-whole")
    fraction_el = item.select_one(".a-price-fraction")

    if whole_el:
        whole_text = re.sub(r"[^\d]", "", whole_el.get_text(strip=True))
        if whole_text:
            fraction_text = "00"
            if fraction_el:
                ft = re.sub(r"[^\d]", "", fraction_el.get_text(strip=True))
                if ft:
                    fraction_text = ft[:2].ljust(2, "0")
            try:
                return float(f"{whole_text}.{fraction_text}")
            except ValueError:
                pass

    return None


def extract_ebay_price(item: Tag) -> float | None:
    """
    Extract price from an eBay search result item.

    Handles range prices by taking the first (lowest) price.
    """
    price_el = item.select_one(".s-item__price")
    if not price_el:
        return None

    text = price_el.get_text(strip=True)
    return normalize_price(text, marketplace="ebay")


# ── Price validation ──────────────────────────────────────────

# Minimum realistic prices by currency (for any product)
MIN_PRICES = {
    "ARS": 500,
    "MXN": 10,
    "USD": 0.50,
    "EUR": 0.50,
    "GBP": 0.50,
    "BRL": 2.0,
}

# Maximum realistic prices by currency
MAX_PRICES = {
    "ARS": 50_000_000,
    "MXN": 500_000,
    "USD": 50_000,
    "EUR": 50_000,
    "GBP": 50_000,
    "BRL": 300_000,
}


def validate_price(
    price: float,
    currency: str,
    marketplace: str = "",
    title: str = "",
) -> bool:
    """
    Validate a parsed price is realistic.

    Returns True if price is valid, False otherwise.
    """
    if price is None or price <= 0:
        logger.warning(
            "Invalid price %s %s for '%s' [%s]",
            price, currency, title[:40], marketplace,
        )
        return False

    min_p = MIN_PRICES.get(currency, 0.01)
    max_p = MAX_PRICES.get(currency, 1_000_000)

    if price < min_p:
        logger.warning(
            "Price too low: %.2f %s (min=%s) for '%s' [%s]",
            price, currency, min_p, title[:40], marketplace,
        )
        return False

    if price > max_p:
        logger.warning(
            "Price too high: %.2f %s (max=%s) for '%s' [%s]",
            price, currency, max_p, title[:40], marketplace,
        )
        return False

    return True
