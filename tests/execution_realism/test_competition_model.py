"""
Tests — Competition Model: ROI signals, popularity, velocity, P(sale) adjustment.
"""
import pytest

from execution_realism.config import CompetitionConfig
from execution_realism.competition_model import CompetitionModel


class TestCompetitionScore:

    def test_no_signals_zero_score(self):
        m = CompetitionModel()
        result = m.estimate(
            roi=0.10, reviews_count=50, estimated_monthly_sales=5,
            competitor_count=5, probability_of_sale=0.7,
        )
        assert result.competition_score == 0.0
        assert result.adjusted_p_sale == 0.7

    def test_high_roi_increases_score(self):
        m = CompetitionModel()
        low_roi = m.estimate(0.15, 50, 5, 5, 0.7)
        high_roi = m.estimate(0.50, 50, 5, 5, 0.7)
        assert high_roi.competition_score > low_roi.competition_score

    def test_popular_product_increases_score(self):
        m = CompetitionModel()
        unpopular = m.estimate(0.15, 50, 5, 5, 0.7)
        popular = m.estimate(0.15, 500, 5, 5, 0.7)
        assert popular.competition_score > unpopular.competition_score

    def test_high_velocity_increases_score(self):
        m = CompetitionModel()
        slow = m.estimate(0.15, 50, 3, 5, 0.7)
        fast = m.estimate(0.15, 50, 25, 5, 0.7)
        assert fast.competition_score > slow.competition_score

    def test_many_competitors_increases_score(self):
        m = CompetitionModel()
        few = m.estimate(0.15, 50, 5, 5, 0.7)
        many = m.estimate(0.15, 50, 5, 30, 0.7)
        assert many.competition_score >= few.competition_score

    def test_score_capped_at_one(self):
        m = CompetitionModel()
        # Max everything
        result = m.estimate(0.80, 1000, 50, 50, 0.7)
        assert result.competition_score <= 1.0


class TestPSaleAdjustment:

    def test_reduces_p_sale(self):
        m = CompetitionModel()
        result = m.estimate(0.50, 500, 25, 20, 0.7)
        assert result.adjusted_p_sale < 0.7

    def test_p_sale_floors_at_001(self):
        cfg = CompetitionConfig(competition_weight_on_p_sale=1.0)
        m = CompetitionModel(cfg)
        result = m.estimate(0.80, 1000, 50, 50, 0.01)
        assert result.adjusted_p_sale >= 0.01

    def test_signals_dict_populated(self):
        m = CompetitionModel()
        result = m.estimate(0.50, 500, 25, 20, 0.7)
        assert "high_roi" in result.signals
