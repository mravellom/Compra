"""
Realism Guard — the final aggregated gate of the Execution Realism Layer.

Runs ALL checks in sequence. Rejects if ANY single check fails.
Returns a full breakdown for observability and audit.

Flow:
1. Price validation
2. Stock check
3. Slippage estimation
4. Latency tracking
5. Competition scoring
6. Execution confidence (composite)

Default = REJECT. Approval requires every gate to pass.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from .competition_model import CompetitionModel
from .config import ExecutionRealismConfig
from .execution_confidence import ExecutionConfidenceCalculator
from .latency_tracker import LatencyTracker
from .models import RealismCheckResult
from .price_validator import PriceValidator
from .slippage_model import SlippageModel
from .stock_validator import StockValidator

logger = logging.getLogger(__name__)


class RealismGuard:
    """Aggregates all execution realism checks into a single gate."""

    def __init__(self, config: ExecutionRealismConfig | None = None) -> None:
        self._cfg = config or ExecutionRealismConfig()
        self._price = PriceValidator(self._cfg.price)
        self._stock = StockValidator(self._cfg.stock)
        self._latency = LatencyTracker(self._cfg.latency)
        self._slippage = SlippageModel(self._cfg.slippage)
        self._competition = CompetitionModel(self._cfg.competition)
        self._confidence = ExecutionConfidenceCalculator(self._cfg)

    def evaluate(
        self,
        # Price inputs
        expected_buy_price: float,
        current_buy_price: float,
        expected_sell_price: float,
        total_fees: float,
        # Stock inputs
        stock_available: int | None = None,
        seller_stockout_count: int = 0,
        # Latency inputs
        detected_at: datetime | None = None,
        decision_at: datetime | None = None,
        # Slippage inputs
        price_volatility: float = 0.0,
        estimated_monthly_sales: float = 0.0,
        # Competition inputs
        roi: float = 0.0,
        reviews_count: int = 0,
        competitor_count: int = 0,
        probability_of_sale: float = 0.5,
    ) -> RealismCheckResult:
        """Run all realism checks and produce final verdict.

        Returns full breakdown even on rejection — for logging and learning.
        """
        reasons: list[str] = []
        now = datetime.now(timezone.utc)

        # ── Gate 1: Price Validation ──────────────────────
        price_check = self._price.validate(
            expected_buy_price=expected_buy_price,
            current_buy_price=current_buy_price,
            expected_sell_price=expected_sell_price,
            total_fees=total_fees,
        )
        if not price_check.is_valid:
            reasons.append(f"price: {price_check.reason}")

        # Use the re-checked buy price for downstream calcs
        effective_buy = current_buy_price if price_check.is_valid else expected_buy_price

        # ── Gate 2: Stock Check ───────────────────────────
        stock_check = self._stock.check(
            stock_available=stock_available,
            seller_stockout_count=seller_stockout_count,
        )
        if not stock_check.available:
            reasons.append(f"stock: {stock_check.reason}")

        # ── Gate 3: Slippage Estimation ───────────────────
        slippage = self._slippage.estimate(
            expected_buy_price=effective_buy,
            expected_sell_price=expected_sell_price,
            total_fees=total_fees,
            price_volatility=price_volatility,
            estimated_monthly_sales=estimated_monthly_sales,
        )
        if not slippage.is_viable:
            reasons.append(f"slippage: {slippage.reason}")

        # ── Gate 4: Latency Tracking ──────────────────────
        detected = detected_at or now
        latency = self._latency.evaluate(
            detected_at=detected,
            decision_at=decision_at,
            execution_at=now,
        )
        if not latency.is_acceptable:
            reasons.append(f"latency: {latency.reason}")

        # ── Gate 5: Competition Scoring ───────────────────
        competition = self._competition.estimate(
            roi=roi,
            reviews_count=reviews_count,
            estimated_monthly_sales=estimated_monthly_sales,
            competitor_count=competitor_count,
            probability_of_sale=probability_of_sale,
        )

        # ── Gate 6: Execution Confidence ──────────────────
        confidence_result = self._confidence.compute(
            price_check=price_check,
            stock_check=stock_check,
            latency=latency,
            competition=competition,
        )
        if not confidence_result.is_acceptable:
            reasons.append(
                f"execution_confidence {confidence_result.execution_confidence:.3f} "
                f"< {self._cfg.min_execution_confidence}"
            )

        # ── Final Verdict ─────────────────────────────────
        approved = len(reasons) == 0

        # Compute final adjusted values
        final_buy = slippage.slipped_buy_price if slippage.is_viable else effective_buy
        final_profit = slippage.profit_after_slippage if slippage.is_viable else (
            expected_sell_price - effective_buy - total_fees
        )
        final_p_sale = competition.adjusted_p_sale

        if approved:
            logger.info(
                "RealismGuard APPROVED: buy=$%.2f profit=$%.2f p_sale=%.2f confidence=%.3f",
                final_buy, final_profit, final_p_sale,
                confidence_result.execution_confidence,
            )
        else:
            logger.info(
                "RealismGuard REJECTED (%d reasons): %s",
                len(reasons), "; ".join(reasons),
            )

        return RealismCheckResult(
            approved=approved,
            rejection_reasons=reasons,
            price_check=price_check,
            stock_check=stock_check,
            slippage=slippage,
            latency=latency,
            competition=competition,
            execution_confidence=confidence_result,
            final_buy_price=round(final_buy, 2),
            final_profit=round(final_profit, 2),
            final_p_sale=round(final_p_sale, 4),
        )
