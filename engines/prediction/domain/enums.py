from enum import Enum


class ModelType(str, Enum):
    BASELINE = "baseline"
    PROPHET = "prophet"
    XGBOOST = "xgboost"


class ForecastHorizon(int, Enum):
    DAYS_7 = 7
    DAYS_14 = 14
    DAYS_30 = 30


class PredictionStatus(str, Enum):
    COMPUTED = "computed"
    STALE = "stale"
    FAILED = "failed"
