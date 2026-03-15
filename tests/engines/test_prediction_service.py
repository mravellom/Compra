"""Tests for the prediction service layer."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from engines.prediction.application.model_registry import create_default_registry
from engines.prediction.application.prediction_service import PredictionService
from engines.prediction.domain.enums import ForecastHorizon, ModelType
from engines.prediction.domain.models import PredictionRequest, PriceForecast, PricePoint


@pytest.fixture
def mock_repo():
    repo = AsyncMock()
    repo.get_price_history = AsyncMock(return_value=[])
    repo.save_prediction = AsyncMock()
    repo.get_predictions = AsyncMock(return_value=[])
    repo.get_products_with_history = AsyncMock(return_value=[1, 2, 3])
    return repo


@pytest.fixture
def mock_publisher():
    pub = AsyncMock()
    pub.publish = AsyncMock()
    return pub


@pytest.fixture
def service(mock_repo, mock_publisher):
    registry = create_default_registry()
    return PredictionService(mock_repo, registry, mock_publisher)


def _make_history(n: int = 20) -> list[PricePoint]:
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        PricePoint(price=100 + i * 0.5, recorded_at=base + timedelta(days=i))
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_predict_no_history(service, mock_repo):
    """Should return failed prediction when no price history exists."""
    mock_repo.get_price_history.return_value = []

    request = PredictionRequest(product_id=999)
    result = await service.predict(request)

    assert result.status == "failed"
    assert result.predicted_price == 0
    mock_repo.save_prediction.assert_not_called()


@pytest.mark.asyncio
async def test_predict_with_history(service, mock_repo, mock_publisher):
    """Should generate a valid prediction and persist it."""
    mock_repo.get_price_history.return_value = _make_history(20)

    request = PredictionRequest(product_id=1, horizon=ForecastHorizon.DAYS_7)
    result = await service.predict(request)

    assert result.status == "computed"
    assert result.predicted_price > 0
    mock_repo.save_prediction.assert_called_once()
    mock_publisher.publish.assert_called_once()


@pytest.mark.asyncio
async def test_predict_explicit_model(service, mock_repo):
    """Should use the explicitly requested model type."""
    mock_repo.get_price_history.return_value = _make_history(20)

    request = PredictionRequest(
        product_id=1, horizon=ForecastHorizon.DAYS_7, model_type=ModelType.BASELINE
    )
    result = await service.predict(request)

    assert result.model_type == "baseline"


@pytest.mark.asyncio
async def test_predict_batch(service, mock_repo):
    """Batch prediction should process all product IDs."""
    mock_repo.get_price_history.return_value = _make_history(10)

    results = await service.predict_batch([1, 2, 3], ForecastHorizon.DAYS_7)

    assert len(results) == 3
    assert mock_repo.save_prediction.call_count == 3


@pytest.mark.asyncio
async def test_get_predictions(service, mock_repo):
    """Should delegate to repository."""
    mock_repo.get_predictions.return_value = [
        PriceForecast(product_id=1, model_type="baseline", horizon_days=7, predicted_price=100)
    ]

    results = await service.get_predictions(1)
    assert len(results) == 1
    mock_repo.get_predictions.assert_called_once_with(1, None)


@pytest.mark.asyncio
async def test_get_top_product_ids(service, mock_repo):
    """Should delegate to repository."""
    ids = await service.get_top_product_ids(10)
    assert ids == [1, 2, 3]
