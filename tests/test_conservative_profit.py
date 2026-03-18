"""
Tests — Conservative Profit Calculation (hardened mode).

Verifies safety margins, hidden cost buffers, and rejection logic.
"""
import pytest

from api.opportunity import (
    ConservativeProfitCalc,
    ConservativeProfitConfig,
    ProfitCalc,
    calculate_conservative_profit,
)


def _make_profit_calc(
    buy: float = 100.0, sell: float = 200.0, fees: float = 30.0,
) -> ProfitCalc:
    net = sell - buy - fees
    return ProfitCalc(
        buy_price_usd=buy,
        sell_price_usd=sell,
        marketplace_fee=fees * 0.6,
        payment_fee=fees * 0.2,
        sell_tax=fees * 0.1,
        import_tax=fees * 0.1,
        domestic_shipping=0.0,
        international_shipping=0.0,
        total_fees=fees,
        net_profit=net,
        roi=net / buy if buy > 0 else 0,
        margin=net / sell if sell > 0 else 0,
    )


class TestConservativeProfitConfig:

    def test_defaults(self):
        cfg = ConservativeProfitConfig()
        assert cfg.safety_multiplier == 0.70
        assert cfg.hidden_cost_buffer_pct == 0.05
        assert cfg.use_min_competitor_price is True
        assert cfg.min_adjusted_profit_usd == 20.0
        assert cfg.min_adjusted_roi == 0.15


class TestCalculateConservativeProfit:

    def test_viable_opportunity_passes(self):
        calc = _make_profit_calc(buy=100, sell=200, fees=30)
        result = calculate_conservative_profit(calc, min_competitor_price_usd=180.0)
        assert result.is_viable is True
        assert result.rejection_reason is None
        assert result.adjusted_buy_price > calc.buy_price_usd  # inflated
        assert result.adjusted_net_profit < calc.net_profit  # haircut applied

    def test_hidden_cost_buffer_inflates_buy(self):
        calc = _make_profit_calc(buy=100, sell=200, fees=30)
        result = calculate_conservative_profit(calc)
        assert result.adjusted_buy_price == pytest.approx(105.0, abs=0.01)

    def test_safety_multiplier_haircuts_profit(self):
        calc = _make_profit_calc(buy=100, sell=200, fees=30)
        # Without min_competitor: adjusted_sell = 200, adjusted_buy = 105, fees = 30
        # raw_profit = 200 - 105 - 30 = 65, adjusted = 65 * 0.70 = 45.5
        result = calculate_conservative_profit(calc, min_competitor_price_usd=None)
        assert result.adjusted_net_profit == pytest.approx(45.5, abs=0.01)

    def test_min_competitor_price_used(self):
        calc = _make_profit_calc(buy=100, sell=200, fees=30)
        result_with = calculate_conservative_profit(calc, min_competitor_price_usd=150.0)
        result_without = calculate_conservative_profit(calc, min_competitor_price_usd=None)
        assert result_with.adjusted_sell_price < result_without.adjusted_sell_price

    def test_reject_low_profit(self):
        calc = _make_profit_calc(buy=100, sell=120, fees=15)
        result = calculate_conservative_profit(calc)
        # net=5, after buffer+haircut will be very low
        assert result.is_viable is False
        assert "adjusted_profit" in result.rejection_reason

    def test_reject_low_roi(self):
        # Profit passes ($20+) but ROI is too thin on expensive item
        calc = _make_profit_calc(buy=300, sell=380, fees=10)
        result = calculate_conservative_profit(calc, min_competitor_price_usd=380.0)
        # adjusted_buy=315, raw_profit=380-315-10=55, adjusted=55*0.7=38.5 (>$20)
        # adjusted_roi=38.5/315≈12.2% (<15%)
        assert result.is_viable is False
        assert "adjusted_roi" in result.rejection_reason

    def test_custom_config(self):
        cfg = ConservativeProfitConfig(
            safety_multiplier=0.50,
            min_adjusted_profit_usd=10.0,
            min_adjusted_roi=0.05,
        )
        calc = _make_profit_calc(buy=100, sell=160, fees=20)
        result = calculate_conservative_profit(calc, config=cfg)
        assert result.adjusted_net_profit == pytest.approx(
            (160 - 105 - 20) * 0.50, abs=0.01
        )

    def test_returns_original_calc(self):
        calc = _make_profit_calc()
        result = calculate_conservative_profit(calc)
        assert result.original is calc
