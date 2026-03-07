"""
Validation Tests — Opportunity Engine (Price Calculations)

Fórmulas verificadas:
  fees        = sell_price * 0.13
  net_profit  = sell_price - buy_price - fees - shipping
  roi         = net_profit / buy_price          (decimal: 0.64)
  roi_percent = (net_profit / buy_price) * 100  (display: 64.0%)

Tabla de verdad: 6 escenarios base + edge cases.
"""
import pytest

from api.opportunity import ProfitCalc, calculate_profit


class TestCalculateProfit:
    """Tabla de verdad exhaustiva con cálculo manual verificable."""

    # ═══════════════════════════════════════════════════════════════
    # Tabla de verdad (fee_rate=13%, shipping=$10)
    #
    # Escenario               | Buy    | Sell   | Fees    | Ship | Net Profit | ROI (dec) | ROI (%)
    # ─────────────────────── | ────── | ────── | ─────── | ──── | ────────── | ───────── | ───────
    # 1. Rentable clásico     | $100   | $200   | $26.00  | $10  | $64.00     | 0.6400    | 64.0%
    # 2. Ejemplo arquitectura | $120   | $200   | $26.00  | $10  | $44.00     | 0.3667    | 36.7%
    # 3. Producto barato      | $50    | $100   | $13.00  | $10  | $27.00     | 0.5400    | 54.0%
    # 4. Margen estrecho      | $200   | $250   | $32.50  | $10  | $7.50      | 0.0375    | 3.8%
    # 5. Mismo precio (pierde)| $100   | $100   | $13.00  | $10  | -$23.00    | -0.2300   | -23.0%
    # 6. Alto volumen         | $500   | $1000  | $130.00 | $10  | $360.00    | 0.7200    | 72.0%
    # ═══════════════════════════════════════════════════════════════

    @pytest.mark.parametrize(
        "buy, sell, exp_fees, exp_profit, exp_roi_decimal, exp_roi_percent",
        [
            (100.0,  200.0,   26.00,   64.00,  0.6400,   64.0),
            (120.0,  200.0,   26.00,   44.00,  0.3667,   36.7),
            ( 50.0,  100.0,   13.00,   27.00,  0.5400,   54.0),
            (200.0,  250.0,   32.50,    7.50,  0.0375,    3.8),
            (100.0,  100.0,   13.00,  -23.00, -0.2300,  -23.0),
            (500.0, 1000.0,  130.00,  360.00,  0.7200,   72.0),
        ],
        ids=[
            "1-profitable-classic",
            "2-architecture-example",
            "3-cheap-product",
            "4-narrow-margin",
            "5-same-price-loss",
            "6-high-volume",
        ],
    )
    def test_truth_table(self, buy, sell, exp_fees, exp_profit, exp_roi_decimal, exp_roi_percent):
        result = calculate_profit(buy, sell)

        # Inputs preservados
        assert result.buy_price == buy
        assert result.sell_price == sell
        assert result.shipping_cost == 10.0

        # Fees = sell_price * 0.13
        assert result.fees == pytest.approx(exp_fees, abs=0.01)

        # Net profit = sell - buy - fees - shipping
        assert result.net_profit == pytest.approx(exp_profit, abs=0.01)

        # ROI decimal = net_profit / buy_price
        assert result.roi == pytest.approx(exp_roi_decimal, abs=0.001)

        # ROI porcentual = (net_profit / buy_price) * 100
        roi_as_percent = result.roi * 100
        assert roi_as_percent == pytest.approx(exp_roi_percent, abs=0.1)

    # ─── Edge case: buy_price = 0 (división por cero) ───────────

    def test_zero_buy_price_no_division_error(self):
        """buy_price=0 → ROI debe ser 0.0, sin ZeroDivisionError."""
        result = calculate_profit(0.0, 100.0)
        assert result.roi == 0.0  # protección contra div/0
        assert result.fees == pytest.approx(13.0, abs=0.01)
        assert result.net_profit == pytest.approx(77.0, abs=0.01)  # 100 - 0 - 13 - 10

    # ─── ROI formula verification ───────────────────────────────

    def test_roi_is_net_profit_over_buy_price(self):
        """Verifica explícitamente: ROI = net_profit / buy_price."""
        result = calculate_profit(100.0, 200.0)
        expected_roi = result.net_profit / result.buy_price
        assert result.roi == pytest.approx(expected_roi, abs=0.0001)

    def test_roi_percent_conversion(self):
        """ROI decimal 0.64 multiplicado por 100 = 64.0%."""
        result = calculate_profit(100.0, 200.0)
        assert (result.roi * 100) == pytest.approx(64.0, abs=0.1)

    # ─── Custom parameters ──────────────────────────────────────

    def test_custom_fee_rate_10_percent(self):
        result = calculate_profit(100.0, 200.0, fee_rate=0.10)
        assert result.fees == pytest.approx(20.0, abs=0.01)
        assert result.net_profit == pytest.approx(70.0, abs=0.01)  # 200-100-20-10

    def test_custom_shipping_25(self):
        result = calculate_profit(100.0, 200.0, shipping_cost=25.0)
        assert result.shipping_cost == 25.0
        assert result.net_profit == pytest.approx(49.0, abs=0.01)  # 200-100-26-25

    def test_zero_fees_and_shipping(self):
        result = calculate_profit(100.0, 200.0, fee_rate=0.0, shipping_cost=0.0)
        assert result.fees == 0.0
        assert result.shipping_cost == 0.0
        assert result.net_profit == pytest.approx(100.0, abs=0.01)
        assert result.roi == pytest.approx(1.0, abs=0.001)

    def test_return_type_is_profit_calc(self):
        assert isinstance(calculate_profit(100.0, 200.0), ProfitCalc)


class TestProfitEdgeCases:

    def test_very_large_numbers(self):
        result = calculate_profit(50_000.0, 100_000.0)
        assert result.fees == pytest.approx(13_000.0, abs=0.01)
        assert result.net_profit == pytest.approx(36_990.0, abs=0.01)
        assert result.roi > 0

    def test_penny_prices(self):
        result = calculate_profit(0.01, 0.02)
        assert isinstance(result.net_profit, float)
        assert isinstance(result.roi, float)

    def test_sell_below_buy_negative_roi(self):
        result = calculate_profit(100.0, 50.0)
        assert result.net_profit < 0
        assert result.roi < 0
        # ROI negativo en porcentaje
        assert (result.roi * 100) < 0

    def test_slight_loss_with_fees(self):
        """$120 sell - $100 buy - $15.60 fees - $10 ship = -$5.60."""
        result = calculate_profit(100.0, 120.0)
        assert result.fees == pytest.approx(15.60, abs=0.01)
        assert result.net_profit == pytest.approx(-5.60, abs=0.01)
        assert result.roi == pytest.approx(-0.056, abs=0.001)
