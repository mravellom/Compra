"""
Tests — Slippage Model: base slippage, volatility, demand, profit viability.
"""
import pytest

from execution_realism.config import SlippageConfig
from execution_realism.slippage_model import SlippageModel


class TestBaseSlippage:

    def test_always_positive(self):
        m = SlippageModel()
        result = m.estimate(100.0, 200.0, 30.0, price_volatility=0, estimated_monthly_sales=0)
        assert result.estimated_slippage_pct > 0
        assert result.slipped_buy_price > 100.0

    def test_slipped_price_higher(self):
        m = SlippageModel()
        result = m.estimate(100.0, 200.0, 30.0, 0.1, 10.0)
        assert result.slipped_buy_price > 100.0


class TestVolatilityEffect:

    def test_high_volatility_more_slippage(self):
        m = SlippageModel()
        stable = m.estimate(100.0, 200.0, 30.0, price_volatility=0.05, estimated_monthly_sales=5)
        volatile = m.estimate(100.0, 200.0, 30.0, price_volatility=0.30, estimated_monthly_sales=5)
        assert volatile.estimated_slippage_pct > stable.estimated_slippage_pct


class TestDemandEffect:

    def test_high_demand_more_slippage(self):
        m = SlippageModel()
        low = m.estimate(100.0, 200.0, 30.0, 0.1, estimated_monthly_sales=2)
        high = m.estimate(100.0, 200.0, 30.0, 0.1, estimated_monthly_sales=25)
        assert high.estimated_slippage_pct > low.estimated_slippage_pct


class TestProfitViability:

    def test_viable_when_profit_ok(self):
        m = SlippageModel(SlippageConfig(min_profit_after_slippage_usd=10.0))
        result = m.estimate(100.0, 200.0, 30.0, 0.1, 10.0)
        assert result.is_viable is True

    def test_rejected_when_slippage_kills_profit(self):
        m = SlippageModel(SlippageConfig(min_profit_after_slippage_usd=10.0))
        # Tight margins: sell=110, buy=100, fees=5 → profit=5 before slippage
        result = m.estimate(100.0, 110.0, 5.0, 0.2, 20.0)
        assert result.is_viable is False
        assert "profit after slippage" in result.reason

    def test_slippage_reduces_profit(self):
        m = SlippageModel()
        result = m.estimate(100.0, 200.0, 30.0, 0.1, 10.0)
        original_profit = 200.0 - 100.0 - 30.0  # 70
        assert result.profit_after_slippage < original_profit
