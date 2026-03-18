"""
Competition Model — estimates how many other resellers are targeting the same opportunity.

High-ROI arbitrage attracts competition. If everyone sees the same opportunity,
the probability of actually selling at the expected price drops.
"""
import logging

from .config import CompetitionConfig
from .models import CompetitionResult

logger = logging.getLogger(__name__)


class CompetitionModel:
    """Estimates competition intensity and adjusts probability of sale."""

    def __init__(self, config: CompetitionConfig | None = None) -> None:
        self._cfg = config or CompetitionConfig()

    def estimate(
        self,
        roi: float,
        reviews_count: int,
        estimated_monthly_sales: float,
        competitor_count: int,
        probability_of_sale: float,
    ) -> CompetitionResult:
        """Estimate competition and reduce P(sale) accordingly.

        Heuristics:
        - High ROI (>30%) → many resellers see the same deal
        - Popular products (many reviews) → more eyes on it
        - High sales velocity → attracts volume sellers
        - High existing competitor count → already crowded

        Args:
            roi: Expected ROI (0.30 = 30%).
            reviews_count: Total reviews on sell-side listings.
            estimated_monthly_sales: Sales velocity.
            competitor_count: Number of existing competitors.
            probability_of_sale: Input P(sale) to adjust.

        Returns:
            CompetitionResult with score and adjusted P(sale).
        """
        cfg = self._cfg
        signals: dict[str, float] = {}
        score = 0.0

        # Signal 1: High ROI attracts competition
        if roi > cfg.high_roi_threshold:
            excess = min(1.0, (roi - cfg.high_roi_threshold) / cfg.high_roi_threshold)
            contribution = cfg.high_roi_score * (0.5 + 0.5 * excess)
            score += contribution
            signals["high_roi"] = round(contribution, 3)

        # Signal 2: Popular product (many reviews = many eyes)
        if reviews_count > cfg.popular_product_reviews_threshold:
            ratio = min(2.0, reviews_count / cfg.popular_product_reviews_threshold)
            contribution = cfg.popular_product_score * min(1.0, ratio - 1.0 + 0.5)
            score += contribution
            signals["popular_product"] = round(contribution, 3)

        # Signal 3: High velocity = volume sellers
        if estimated_monthly_sales > cfg.high_velocity_threshold:
            ratio = min(2.0, estimated_monthly_sales / cfg.high_velocity_threshold)
            contribution = cfg.high_velocity_score * min(1.0, ratio - 1.0 + 0.5)
            score += contribution
            signals["high_velocity"] = round(contribution, 3)

        # Signal 4: Existing competitor density
        # 20+ competitors → additional 0.15 competition score
        if competitor_count > 15:
            density = min(0.15, (competitor_count - 15) / 50.0 * 0.15)
            score += density
            signals["competitor_density"] = round(density, 3)

        score = min(1.0, score)

        # Adjust P(sale): higher competition → lower probability
        adjusted_p_sale = probability_of_sale * (
            1.0 - score * cfg.competition_weight_on_p_sale
        )
        adjusted_p_sale = max(0.01, adjusted_p_sale)

        logger.debug(
            "Competition: score=%.2f p_sale=%.3f→%.3f signals=%s",
            score, probability_of_sale, adjusted_p_sale, signals,
        )

        return CompetitionResult(
            competition_score=round(score, 4),
            adjusted_p_sale=round(adjusted_p_sale, 4),
            signals=signals,
        )
