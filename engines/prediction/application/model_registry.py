"""Strategy Pattern registry for prediction models.

Register and retrieve prediction strategies by ModelType.
Supports dynamic registration for extensibility.
"""
import logging

from ..domain.enums import ModelType
from ..domain.strategies import (
    BaselineStrategy,
    PredictionStrategy,
    ProphetStrategy,
    XGBoostStrategy,
)

logger = logging.getLogger(__name__)


class ModelRegistry:
    """Registry mapping ModelType -> PredictionStrategy instances."""

    def __init__(self) -> None:
        self._strategies: dict[ModelType, PredictionStrategy] = {}

    def register(self, strategy: PredictionStrategy) -> None:
        self._strategies[strategy.model_type] = strategy
        logger.debug("Registered prediction strategy: %s", strategy.model_type.value)

    def get(self, model_type: ModelType) -> PredictionStrategy:
        strategy = self._strategies.get(model_type)
        if strategy is None:
            raise KeyError(f"No strategy registered for {model_type.value}")
        return strategy

    def get_or_fallback(self, model_type: ModelType) -> PredictionStrategy:
        """Get requested strategy, falling back to baseline."""
        try:
            return self.get(model_type)
        except KeyError:
            logger.warning("Strategy %s not found, falling back to baseline", model_type.value)
            return self.get(ModelType.BASELINE)

    def available(self) -> list[ModelType]:
        return list(self._strategies.keys())

    def select_best(self, data_points: int) -> PredictionStrategy:
        """Auto-select the best available strategy based on data volume."""
        if data_points >= 30 and ModelType.PROPHET in self._strategies:
            return self._strategies[ModelType.PROPHET]
        if data_points >= 14 and ModelType.XGBOOST in self._strategies:
            return self._strategies[ModelType.XGBOOST]
        return self._strategies[ModelType.BASELINE]


def create_default_registry() -> ModelRegistry:
    """Create a registry with all available strategies."""
    registry = ModelRegistry()
    registry.register(BaselineStrategy())
    registry.register(XGBoostStrategy())
    registry.register(ProphetStrategy())
    return registry
