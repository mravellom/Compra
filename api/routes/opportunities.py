from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, Query

from ..database import get_db
from ..category_engine import refresh_category_stats
from ..lifecycle_engine import update_all_lifecycles
from ..models import AlertConfig, MasterProduct, Opportunity, ProductListing
from ..opportunity import detect_opportunities
from ..schemas import DashboardStats, OpportunityDetail, OpportunityOut, RelatedListing
from ..telegram_bot import notify_opportunity

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


@router.get("/stats", response_model=DashboardStats)
async def dashboard_stats(db: AsyncSession = Depends(get_db)):
    """Estadisticas generales para el dashboard."""
    result = await db.execute(
        select(
            func.count(Opportunity.id),
            func.coalesce(func.avg(Opportunity.roi), 0),
            func.coalesce(func.avg(Opportunity.net_profit), 0),
            func.max(Opportunity.created_at),
        ).where(Opportunity.status == "active")
    )
    row = result.one()

    # Top marketplace (el que mas aparece como sell)
    top_mp_result = await db.execute(
        select(Opportunity.sell_marketplace, func.count())
        .where(Opportunity.status == "active")
        .group_by(Opportunity.sell_marketplace)
        .order_by(func.count().desc())
        .limit(1)
    )
    top_mp_row = top_mp_result.first()

    return DashboardStats(
        total_opportunities=row[0],
        avg_roi=round(float(row[1]), 4),
        avg_profit=round(float(row[2]), 2),
        top_marketplace=top_mp_row[0] if top_mp_row else None,
        last_scan=row[3],
    )


@router.post("/scan", response_model=dict)
async def scan_opportunities(db: AsyncSession = Depends(get_db)):
    """Ejecuta un escaneo de oportunidades y notifica via Telegram."""
    new_opps = await detect_opportunities(db)

    # Notificar a usuarios con alertas configuradas
    alerts_result = await db.execute(
        select(AlertConfig).where(AlertConfig.enabled.is_(True))
    )
    alert_configs = list(alerts_result.scalars().all())

    notifications_sent = 0
    for opp in new_opps:
        product = await db.get(MasterProduct, opp.master_product_id)
        buy_listing = await db.get(ProductListing, opp.buy_listing_id)
        sell_listing = await db.get(ProductListing, opp.sell_listing_id)

        for config in alert_configs:
            if not config.telegram_chat_id:
                continue
            if opp.roi < config.min_roi:
                continue
            if float(opp.net_profit) < float(config.min_profit):
                continue
            if config.max_buy_price and float(opp.buy_price) > float(config.max_buy_price):
                continue

            sent = await notify_opportunity(
                chat_id=config.telegram_chat_id,
                product_name=product.canonical_name if product else "Unknown",
                buy_price=float(opp.buy_price),
                sell_price=float(opp.sell_price),
                net_profit=float(opp.net_profit),
                roi=opp.roi,
                buy_marketplace=opp.buy_marketplace,
                sell_marketplace=opp.sell_marketplace,
                buy_url=buy_listing.url if buy_listing else "",
                sell_url=sell_listing.url if sell_listing else "",
            )
            if sent:
                notifications_sent += 1

    # Update lifecycle tracking for all active opportunities
    lifecycle_count = await update_all_lifecycles(db)

    # Refresh category-level analytics after scan
    cat_stats = await refresh_category_stats(db)

    await db.commit()

    return {
        "new_opportunities": len(new_opps),
        "notifications_sent": notifications_sent,
        "lifecycles_updated": lifecycle_count,
        "categories_analyzed": len(cat_stats),
    }


@router.get("", response_model=list[OpportunityOut])
async def list_opportunities(
    min_roi: float | None = Query(None, description="ROI minimo (0.20 = 20%)"),
    max_buy_price: float | None = Query(None),
    category: str | None = Query(None),
    marketplace: str | None = Query(None),
    status: str = Query("active"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Lista oportunidades con filtros opcionales."""
    query = (
        select(
            Opportunity,
            MasterProduct.canonical_name,
            MasterProduct.category,
        )
        .join(MasterProduct, Opportunity.master_product_id == MasterProduct.id)
        .where(Opportunity.status == status)
        .order_by(Opportunity.opportunity_score.desc())
    )

    if min_roi is not None:
        query = query.where(Opportunity.roi >= min_roi)
    if max_buy_price is not None:
        query = query.where(Opportunity.buy_price <= max_buy_price)
    if category:
        query = query.where(MasterProduct.category == category)
    if marketplace:
        query = query.where(
            (Opportunity.buy_marketplace == marketplace)
            | (Opportunity.sell_marketplace == marketplace)
        )

    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    rows = result.all()

    opportunities = []
    for opp, product_name, _cat in rows:
        buy_listing = await db.get(ProductListing, opp.buy_listing_id)
        sell_listing = await db.get(ProductListing, opp.sell_listing_id)

        opportunities.append(OpportunityOut(
            id=opp.id,
            product_name=product_name,
            buy_price=float(opp.buy_price),
            sell_price=float(opp.sell_price),
            estimated_sell_price=float(opp.estimated_sell_price) if opp.estimated_sell_price else None,
            fees=float(opp.fees),
            shipping_cost=float(opp.shipping_cost),
            net_profit=float(opp.net_profit),
            roi=opp.roi,
            buy_marketplace=opp.buy_marketplace,
            sell_marketplace=opp.sell_marketplace,
            buy_url=buy_listing.url if buy_listing else "",
            sell_url=sell_listing.url if sell_listing else "",
            image_url=buy_listing.image_url if buy_listing else None,
            status=opp.status,
            created_at=opp.created_at,
            marketplace_fee=float(opp.marketplace_fee or 0),
            payment_fee=float(opp.payment_fee or 0),
            import_tax=float(opp.import_tax or 0),
            domestic_shipping=float(opp.domestic_shipping or 0),
            international_shipping=float(opp.international_shipping or 0),
            sales_velocity_score=opp.sales_velocity_score or 0,
            competition_score=opp.competition_score or 0,
            price_stability_score=opp.price_stability_score or 0,
            opportunity_score=opp.opportunity_score or 0,
            confidence_level=opp.confidence_level or "low",
            competitor_count=opp.competitor_count or 0,
            avg_market_price=float(opp.avg_market_price) if opp.avg_market_price else None,
            lowest_competitor_price=float(opp.lowest_competitor_price) if opp.lowest_competitor_price else None,
            market_depth_score=opp.market_depth_score or 0,
            estimated_daily_sales=opp.estimated_daily_sales or 0,
            estimated_monthly_sales=opp.estimated_monthly_sales or 0,
            scalability_level=opp.scalability_level or "low",
            demand_trend_score=opp.demand_trend_score or 50,
            demand_trend_label=opp.demand_trend_label or "stable",
            decay_rate=opp.decay_rate or 0,
            lifetime_hours=opp.lifetime_hours or 0,
            urgency_score=opp.urgency_score or 50,
            lifecycle_label=opp.lifecycle_label or "fresh",
            capital_required=float(opp.capital_required or 0),
            capital_efficiency_score=opp.capital_efficiency_score or 0,
            capital_tier=opp.capital_tier or "medium",
            recommended_quantity=opp.recommended_quantity or 1,
        ))

    return opportunities


@router.get("/{opportunity_id}", response_model=OpportunityDetail)
async def get_opportunity(opportunity_id: int, db: AsyncSession = Depends(get_db)):
    """Detalle completo de una oportunidad con listings relacionados."""
    result = await db.execute(
        select(Opportunity, MasterProduct)
        .join(MasterProduct, Opportunity.master_product_id == MasterProduct.id)
        .where(Opportunity.id == opportunity_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    opp, product = row
    buy_listing = await db.get(ProductListing, opp.buy_listing_id)
    sell_listing = await db.get(ProductListing, opp.sell_listing_id)

    # Listings relacionados del mismo master product
    listings_result = await db.execute(
        select(ProductListing)
        .where(ProductListing.master_product_id == product.id)
        .order_by(ProductListing.price.asc())
    )
    related = [
        RelatedListing(
            id=l.id,
            title=l.title,
            price=float(l.price),
            currency=l.currency,
            marketplace_id=l.marketplace_id,
            url=l.url,
            image_url=l.image_url,
            similarity_score=l.similarity_score,
            scraped_at=l.scraped_at,
        )
        for l in listings_result.scalars().all()
    ]

    return OpportunityDetail(
        id=opp.id,
        product_name=product.canonical_name,
        buy_price=float(opp.buy_price),
        sell_price=float(opp.sell_price),
        fees=float(opp.fees),
        shipping_cost=float(opp.shipping_cost),
        net_profit=float(opp.net_profit),
        roi=opp.roi,
        buy_marketplace=opp.buy_marketplace,
        sell_marketplace=opp.sell_marketplace,
        buy_url=buy_listing.url if buy_listing else "",
        sell_url=sell_listing.url if sell_listing else "",
        image_url=buy_listing.image_url if buy_listing else None,
        status=opp.status,
        created_at=opp.created_at,
        category=product.category,
        brand=product.brand,
        model=product.model,
        related_listings=related,
    )


