from enum import Enum


class TrendDirection(str, Enum):
    RISING = "rising"
    FALLING = "falling"
    STABLE = "stable"


class TrendStrength(str, Enum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class TrendType(str, Enum):
    STABLE = "stable"
    RISING = "rising"
    FALLING = "falling"
    BREAKOUT = "breakout"
