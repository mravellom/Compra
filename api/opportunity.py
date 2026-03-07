"""
Opportunity Engine: calcula profit y ROI entre pares de listings.
Convierte precios a USD antes de comparar.
"""
import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import MasterProduct, Opportunity, ProductListing

logger = logging.getLogger(__name__)

DEFAULT_FEE_RATE = 0.13       # 13% comision del marketplace
DEFAULT_SHIPPING = 10.00      # costo de envio estimado en USD
MIN_PROFIT_THRESHOLD = 5.00   # ganancia minima en USD para considerar oportunidad

# Cache de tasas de cambio (se actualiza por scan)
_exchange_rates: dict[str, float] = {}


async def fetch_exchange_rates() -> dict[str, float]:
    """Obtiene tasas de cambio actualizadas (moneda -> 1 USD)."""
    global _exchange_rates
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://open.er-api.com/v6/latest/USD")
            resp.raise_for_status()
            data = resp.json()
            _exchange_rates = data["rates"]
            _exchange_rates["USD"] = 1.0
            logger.info(
                "Exchange rates updated: ARS=%.2f, MXN=%.2f",
                _exchange_rates.get("ARS", 0),
                _exchange_rates.get("MXN", 0),
            )
    except Exception:
        logger.error("Failed to fetch exchange rates, using fallback", exc_info=True)
        if not _exchange_rates:
            _exchange_rates = {"USD": 1.0, "ARS": 1450.0, "MXN": 17.8, "EUR": 0.86, "GBP": 0.75}
    return _exchange_rates


def to_usd(price: float, currency: str) -> float:
    """Convierte un precio a USD usando las tasas cacheadas."""
    if currency == "USD":
        return price
    rate = _exchange_rates.get(currency)
    if not rate or rate == 0:
        logger.warning("No exchange rate for %s, treating as USD", currency)
        return price
    return price / rate


@dataclass
class ProfitCalc:
    buy_price: float
    sell_price: float
    fees: float
    shipping_cost: float
    net_profit: float
    roi: float


def calculate_profit(
    buy_price_usd: float,
    sell_price_usd: float,
    fee_rate: float = DEFAULT_FEE_RATE,
    shipping_cost: float = DEFAULT_SHIPPING,
) -> ProfitCalc:
    """
    Calcula el profit neto y ROI de una operacion de arbitraje.
    Todos los precios deben estar en USD.
    """
    fees = sell_price_usd * fee_rate
    net_profit = sell_price_usd - buy_price_usd - fees - shipping_cost
    roi = net_profit / buy_price_usd if buy_price_usd > 0 else 0.0

    return ProfitCalc(
        buy_price=buy_price_usd,
        sell_price=sell_price_usd,
        fees=round(fees, 2),
        shipping_cost=shipping_cost,
        net_profit=round(net_profit, 2),
        roi=round(roi, 4),
    )


async def detect_opportunities(
    db: AsyncSession,
    fee_rate: float = DEFAULT_FEE_RATE,
    shipping_cost: float = DEFAULT_SHIPPING,
) -> list[Opportunity]:
    """
    Escanea listings agrupados por master_product y detecta oportunidades
    de arbitraje entre marketplaces distintos.
    Convierte precios a USD antes de comparar.
    """
    await fetch_exchange_rates()

    # Obtener master products que tienen listings en al menos 2 marketplaces
    result = await db.execute(
        text("""
            SELECT master_product_id
            FROM product_listings
            GROUP BY master_product_id
            HAVING COUNT(DISTINCT marketplace_id) >= 2
        """)
    )
    candidate_ids = [row[0] for row in result.fetchall()]

    if not candidate_ids:
        logger.info("No candidates with multi-marketplace listings found")
        return []

    new_opportunities: list[Opportunity] = []

    for mp_id in candidate_ids:
        listings_result = await db.execute(
            select(ProductListing)
            .where(ProductListing.master_product_id == mp_id)
            .order_by(ProductListing.price)
        )
        listings = list(listings_result.scalars().all())

        if len(listings) < 2:
            continue

        # Agrupar por marketplace: precio mas bajo (en USD) de cada uno
        by_marketplace: dict[str, ProductListing] = {}
        for listing in listings:
            price_usd = to_usd(float(listing.price), listing.currency)
            if listing.marketplace_id not in by_marketplace:
                by_marketplace[listing.marketplace_id] = listing
            else:
                existing_usd = to_usd(
                    float(by_marketplace[listing.marketplace_id].price),
                    by_marketplace[listing.marketplace_id].currency,
                )
                if price_usd < existing_usd:
                    by_marketplace[listing.marketplace_id] = listing

        if len(by_marketplace) < 2:
            continue

        marketplace_listings = list(by_marketplace.values())

        # Comparar todos los pares
        for i, buy in enumerate(marketplace_listings):
            for sell in marketplace_listings[i + 1:]:
                # Probar ambas direcciones
                for b, s in [(buy, sell), (sell, buy)]:
                    buy_usd = to_usd(float(b.price), b.currency)
                    sell_usd = to_usd(float(s.price), s.currency)

                    if sell_usd <= buy_usd:
                        continue

                    calc = calculate_profit(buy_usd, sell_usd, fee_rate, shipping_cost)

                    if calc.net_profit < MIN_PROFIT_THRESHOLD:
                        continue

                    # Verificar que no exista ya esta oportunidad
                    existing = await db.execute(
                        select(Opportunity).where(
                            Opportunity.buy_listing_id == b.id,
                            Opportunity.sell_listing_id == s.id,
                            Opportunity.status == "active",
                        )
                    )
                    if existing.scalar_one_or_none():
                        continue

                    opp = Opportunity(
                        master_product_id=mp_id,
                        buy_listing_id=b.id,
                        sell_listing_id=s.id,
                        buy_price=round(buy_usd, 2),
                        sell_price=round(sell_usd, 2),
                        fees=calc.fees,
                        shipping_cost=calc.shipping_cost,
                        net_profit=calc.net_profit,
                        roi=calc.roi,
                        buy_marketplace=b.marketplace_id,
                        sell_marketplace=s.marketplace_id,
                    )
                    db.add(opp)
                    new_opportunities.append(opp)

                    logger.info(
                        "NEW OPP: %s | buy $%.2f USD (%s/%s) -> sell $%.2f USD (%s/%s) | profit $%.2f | ROI %.1f%%",
                        mp_id, buy_usd, b.marketplace_id, b.currency,
                        sell_usd, s.marketplace_id, s.currency,
                        calc.net_profit, calc.roi * 100,
                    )

    if new_opportunities:
        await db.commit()
        logger.info("Detected %d new opportunities", len(new_opportunities))

    return new_opportunities
