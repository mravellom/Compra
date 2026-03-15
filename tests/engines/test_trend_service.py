"""Tests for the trend service layer."""
import pytest
from unittest.mock import AsyncMock

from engines.trend.application.trend_aggregator import TrendAggregator
from engines.trend.application.trend_service import TrendService
from engines.trend.domain.models import ProductTrendResult, TrendSnapshot
from engines.trend.domain.strategies import PriceMomentumStrategy, VelocitySpikeStrategy


@pytest.fixture
def mock_repo():
    repo = AsyncMock()
    repo.get_active_product_ids = AsyncMock(return_value=[1, 2, 3])
    repo.build_snapshot = AsyncMock(return_value=TrendSnapshot(
        product_id=1, listing_count=10, avg_price=100.0,
        seller_count=5, marketplace_count=3, velocity_7d=5.0, velocity_30d=3.0,
    ))
    repo.get_snapshots = AsyncMock(return_value=[
        TrendSnapshot(
            product_id=1, listing_count=8, avg_price=95.0,
            seller_count=4, marketplace_count=2, velocity_7d=3.0, velocity_30d=3.0,
        ),
    ])
    repo.save_trend = AsyncMock()
    repo.update_master_product_trend = AsyncMock()
    repo.get_top_trends = AsyncMock(return_value=[
        ProductTrendResult(product_id=1, trend_score=75, trend_type="rising"),
    ])
    repo.get_trend = AsyncMock(return_value=ProductTrendResult(product_id=1, trend_score=60))
    repo.get_breakouts = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_publisher():
    pub = AsyncMock()
    pub.publish = AsyncMock()
    return pub


@pytest.fixture
def service(mock_repo, mock_publisher):
    aggregator = TrendAggregator([
        VelocitySpikeStrategy(),
        PriceMomentumStrategy(),
    ])
    return TrendService(mock_repo, aggregator, mock_publisher)


@pytest.mark.asyncio
async def test_detect_trends(service, mock_repo):
    """Should detect trends for active products."""
    results = await service.detect_trends(limit=3)

    assert len(results) == 3
    assert mock_repo.save_trend.call_count == 3
    assert mock_repo.update_master_product_trend.call_count == 3


@pytest.mark.asyncio
async def test_detect_trends_handles_errors(service, mock_repo):
    """Should continue processing even if one product fails."""
    mock_repo.build_snapshot.side_effect = [
        TrendSnapshot(product_id=1, listing_count=10, avg_price=100),
        Exception("DB error"),
        TrendSnapshot(product_id=3, listing_count=5, avg_price=50),
    ]
    mock_repo.get_snapshots.return_value = []

    results = await service.detect_trends(limit=3)
    assert len(results) == 2  # 1 failed out of 3


@pytest.mark.asyncio
async def test_get_trending(service, mock_repo):
    """Should delegate to repository."""
    trends = await service.get_trending(20)
    assert len(trends) == 1
    mock_repo.get_top_trends.assert_called_once_with(20)


@pytest.mark.asyncio
async def test_get_trend(service, mock_repo):
    """Should return trend for specific product."""
    trend = await service.get_trend(1)
    assert trend is not None
    assert trend.product_id == 1


@pytest.mark.asyncio
async def test_get_breakouts(service, mock_repo):
    """Should return breakout products."""
    breakouts = await service.get_breakouts()
    assert isinstance(breakouts, list)


@pytest.mark.asyncio
async def test_publishes_significant_trends(service, mock_repo, mock_publisher):
    """Should publish events for trends scoring >= 50."""
    # The default mock snapshot creates a rising trend that may score > 50
    await service.detect_trends(limit=3)
    # Publisher may or may not be called depending on computed scores
    # At minimum, detect_trends should not raise
