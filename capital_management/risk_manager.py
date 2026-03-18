"""
Risk Manager — circuit breakers for capital protection.

Tracks:
- Consecutive losses → reduce position sizes
- Portfolio drawdown → halt all executions
- Capital stop-loss → pause system
- Post-loss cooldown → time-based gate

This is the kill switch. When it says stop, everything stops.
"""
import logging
from datetime import datetime, timedelta, timezone

from .config import CapitalConfig
from .models import PortfolioState, RiskCheckResult, RiskLevel

logger = logging.getLogger(__name__)


class RiskManager:
    """Portfolio-level risk management with circuit breakers."""

    def __init__(self, config: CapitalConfig) -> None:
        self._cfg = config.risk

    def check(self, portfolio: PortfolioState) -> RiskCheckResult:
        """Evaluate whether trading should continue.

        Returns:
            RiskCheckResult with can_execute, risk_level, and adjustment_factor.
        """
        reasons: list[str] = []
        can_execute = True
        adjustment_factor = 1.0
        cfg = self._cfg

        # ── Circuit Breaker 1: Drawdown halt ──────────────
        if portfolio.drawdown_pct >= cfg.max_drawdown_pct:
            can_execute = False
            reasons.append(
                f"HALT: drawdown {portfolio.drawdown_pct:.1%} >= max {cfg.max_drawdown_pct:.0%}"
            )

        # ── Circuit Breaker 2: Capital stop-loss ──────────
        initial = portfolio.peak_capital
        if initial > 0:
            capital_loss_pct = (initial - portfolio.total_capital) / initial
            if capital_loss_pct >= cfg.capital_stop_loss_pct:
                can_execute = False
                reasons.append(
                    f"HALT: capital loss {capital_loss_pct:.1%} >= stop-loss {cfg.capital_stop_loss_pct:.0%}"
                )

        # ── Circuit Breaker 3: Daily loss cap ─────────────
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if portfolio.daily_loss_reset_date == today:
            if portfolio.daily_loss_usd >= cfg.max_daily_loss_usd:
                can_execute = False
                reasons.append(
                    f"HALT: daily loss ${portfolio.daily_loss_usd:.2f} >= cap ${cfg.max_daily_loss_usd:.2f}"
                )

        # ── Consecutive loss streak → reduce sizing ───────
        if portfolio.consecutive_losses >= cfg.consecutive_loss_limit:
            adjustment_factor *= cfg.sizing_reduction_on_streak
            reasons.append(
                f"REDUCE: {portfolio.consecutive_losses} consecutive losses "
                f"(>= {cfg.consecutive_loss_limit}) → sizing ×{cfg.sizing_reduction_on_streak}"
            )

        # ── Cooldown after last loss ──────────────────────
        if portfolio.last_loss_at is not None:
            cooldown_end = portfolio.last_loss_at + timedelta(
                minutes=cfg.cooldown_after_loss_minutes
            )
            now = datetime.now(timezone.utc)
            if now < cooldown_end:
                remaining = (cooldown_end - now).total_seconds() / 60.0
                can_execute = False
                reasons.append(
                    f"COOLDOWN: {remaining:.0f}min remaining after last loss"
                )

        # ── Risk level classification ─────────────────────
        if not can_execute:
            risk_level = RiskLevel.CRITICAL
        elif adjustment_factor < 0.7:
            risk_level = RiskLevel.HIGH
        elif portfolio.drawdown_pct > cfg.max_drawdown_pct * 0.5:
            risk_level = RiskLevel.MEDIUM
        else:
            risk_level = RiskLevel.LOW

        if not can_execute:
            logger.warning(
                "RiskManager BLOCKED execution: %s", "; ".join(reasons),
            )
        elif adjustment_factor < 1.0:
            logger.info(
                "RiskManager adjusting: factor=%.2f reasons=%s",
                adjustment_factor, "; ".join(reasons),
            )

        return RiskCheckResult(
            can_execute=can_execute,
            risk_level=risk_level,
            adjustment_factor=round(adjustment_factor, 4),
            reasons=reasons,
        )
