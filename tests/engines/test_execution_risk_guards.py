"""Tests for execution engine risk guards."""
import os
import pytest

from engines.execution.domain.enums import OrderType
from engines.execution.domain.models import PortfolioSummary, TradeOrder
from engines.execution.domain.risk_guards import (
    DailyLimitGuard,
    DuplicateGuard,
    MaxExposureGuard,
    MinProfitGuard,
    MinROIGuard,
    SingleOrderLimitGuard,
)
from engines.execution.application.risk_engine import RiskEngine


def _order(price: float = 100, quantity: int = 1, profit: float = 20) -> TradeOrder:
    return TradeOrder(
        product_id=1,
        order_type=OrderType.BUY,
        marketplace="amazon_us",
        price=price,
        quantity=quantity,
        total_cost=price * quantity,
        estimated_profit=profit,
    )


def _portfolio(
    exposure: float = 0, executed_today: int = 0
) -> PortfolioSummary:
    return PortfolioSummary(
        total_exposure=exposure,
        open_orders=0,
        executed_today=executed_today,
    )


# ─── Individual guard tests ─────────────────────────────────────────

class TestMaxExposureGuard:
    def test_within_limit(self, monkeypatch):
        monkeypatch.setenv("MAX_TOTAL_EXPOSURE", "10000")
        guard = MaxExposureGuard()
        passed, _ = guard.evaluate(_order(100), _portfolio(5000))
        assert passed

    def test_exceeds_limit(self, monkeypatch):
        monkeypatch.setenv("MAX_TOTAL_EXPOSURE", "500")
        guard = MaxExposureGuard()
        passed, reason = guard.evaluate(_order(300), _portfolio(300))
        assert not passed
        assert "exceed" in reason.lower()


class TestSingleOrderLimitGuard:
    def test_within_limit(self, monkeypatch):
        monkeypatch.setenv("MAX_SINGLE_ORDER", "2000")
        guard = SingleOrderLimitGuard()
        passed, _ = guard.evaluate(_order(100), _portfolio())
        assert passed

    def test_exceeds_limit(self, monkeypatch):
        monkeypatch.setenv("MAX_SINGLE_ORDER", "50")
        guard = SingleOrderLimitGuard()
        passed, reason = guard.evaluate(_order(100), _portfolio())
        assert not passed
        assert "single order" in reason.lower()


class TestDailyLimitGuard:
    def test_within_limit(self, monkeypatch):
        monkeypatch.setenv("MAX_DAILY_ORDERS", "50")
        guard = DailyLimitGuard()
        passed, _ = guard.evaluate(_order(), _portfolio(executed_today=10))
        assert passed

    def test_at_limit(self, monkeypatch):
        monkeypatch.setenv("MAX_DAILY_ORDERS", "10")
        guard = DailyLimitGuard()
        passed, reason = guard.evaluate(_order(), _portfolio(executed_today=10))
        assert not passed
        assert "daily" in reason.lower()


class TestMinROIGuard:
    def test_sufficient_roi(self, monkeypatch):
        monkeypatch.setenv("EXECUTION_MIN_ROI", "0.05")
        guard = MinROIGuard()
        passed, _ = guard.evaluate(_order(100, profit=20), _portfolio())
        assert passed  # 20% ROI > 5%

    def test_insufficient_roi(self, monkeypatch):
        monkeypatch.setenv("EXECUTION_MIN_ROI", "0.30")
        guard = MinROIGuard()
        passed, reason = guard.evaluate(_order(100, profit=5), _portfolio())
        assert not passed
        assert "roi" in reason.lower()


class TestMinProfitGuard:
    def test_sufficient_profit(self, monkeypatch):
        monkeypatch.setenv("EXECUTION_MIN_PROFIT", "5.0")
        guard = MinProfitGuard()
        passed, _ = guard.evaluate(_order(profit=10), _portfolio())
        assert passed

    def test_insufficient_profit(self, monkeypatch):
        monkeypatch.setenv("EXECUTION_MIN_PROFIT", "15.0")
        guard = MinProfitGuard()
        passed, reason = guard.evaluate(_order(profit=5), _portfolio())
        assert not passed


class TestDuplicateGuard:
    def test_always_passes_basic(self):
        guard = DuplicateGuard()
        passed, _ = guard.evaluate(_order(), _portfolio())
        assert passed


# ─── RiskEngine composite tests ─────────────────────────────────────

class TestRiskEngine:
    def test_all_guards_pass(self, monkeypatch):
        monkeypatch.setenv("MAX_TOTAL_EXPOSURE", "100000")
        monkeypatch.setenv("MAX_SINGLE_ORDER", "10000")
        monkeypatch.setenv("MAX_DAILY_ORDERS", "100")
        monkeypatch.setenv("EXECUTION_MIN_ROI", "0.01")
        monkeypatch.setenv("EXECUTION_MIN_PROFIT", "1.0")

        engine = RiskEngine()
        assessment = engine.assess(_order(100, profit=20), _portfolio())

        assert assessment.passed
        assert len(assessment.guards_failed) == 0
        assert len(assessment.guards_passed) == 6  # all 6 default guards

    def test_multiple_guards_fail(self, monkeypatch):
        monkeypatch.setenv("MAX_TOTAL_EXPOSURE", "50")
        monkeypatch.setenv("MAX_SINGLE_ORDER", "50")
        monkeypatch.setenv("MAX_DAILY_ORDERS", "100")
        monkeypatch.setenv("EXECUTION_MIN_ROI", "0.50")
        monkeypatch.setenv("EXECUTION_MIN_PROFIT", "1.0")

        engine = RiskEngine()
        assessment = engine.assess(_order(100, profit=5), _portfolio(exposure=40))

        assert not assessment.passed
        assert len(assessment.guards_failed) >= 2
        assert len(assessment.reasons) >= 2

    def test_add_custom_guard(self, monkeypatch):
        monkeypatch.setenv("MAX_TOTAL_EXPOSURE", "100000")
        monkeypatch.setenv("MAX_SINGLE_ORDER", "10000")
        monkeypatch.setenv("MAX_DAILY_ORDERS", "100")
        monkeypatch.setenv("EXECUTION_MIN_ROI", "0.01")
        monkeypatch.setenv("EXECUTION_MIN_PROFIT", "1.0")

        from engines.execution.domain.risk_guards import RiskGuard

        class AlwaysFailGuard(RiskGuard):
            @property
            def name(self) -> str:
                return "always_fail"

            def evaluate(self, order, portfolio):
                return False, "Always fails"

        engine = RiskEngine()
        engine.add_guard(AlwaysFailGuard())
        assessment = engine.assess(_order(profit=20), _portfolio())

        assert not assessment.passed
        assert "always_fail" in assessment.guards_failed
