"""
Tests for the Capital Manager — execution discipline layer.

Covers:
  1. Position sizing correctness (confidence scaling)
  2. Capital never exceeded
  3. Concurrent trade limits
  4. Opportunity ranking
  5. Per-product and per-marketplace cooldowns
  6. Stop conditions (drawdown, daily loss, success rate)
  7. Dry-run mode
  8. Trade lifecycle (win/loss recording)
  9. Metrics computation
  10. Edge cases (empty batch, single candidate, halted state)
"""
import time

import pytest

from api.capital_manager import (
    CapitalManager,
    CapitalMetrics,
    ExecutionDecision,
    ManagerConfig,
    OpportunityCandidate,
)
from capital_management.config import get_config
from capital_management.models import PortfolioState


# ── Helpers ──────────────────────────────────────────────────

def _portfolio(capital: float = 10000.0, **kw) -> PortfolioState:
    """Create a fresh portfolio."""
    return PortfolioState(
        total_capital=capital,
        available_capital=capital,
        **kw,
    )


def _candidate(
    opp_id: int = 1,
    product_id: int = 100,
    profit: float = 50.0,
    risk: float = 30.0,
    confidence: float = 0.85,
    capital_required: float = 200.0,
    **kw,
) -> OpportunityCandidate:
    return OpportunityCandidate(
        opportunity_id=opp_id,
        product_id=product_id,
        buy_marketplace=kw.get("buy_mp", "amazon_us"),
        sell_marketplace=kw.get("sell_mp", "mercadolibre_mx"),
        category=kw.get("category", "headphones"),
        buy_price=100.0,
        sell_price=180.0,
        expected_profit=profit,
        expected_roi=profit / 100.0,
        opportunity_score=kw.get("score", 70.0),
        risk_score=risk,
        execution_confidence=confidence,
        capital_required=capital_required,
    )


# ── Ranking Tests ────────────────────────────────────────────

class TestRanking:

    def test_higher_confidence_ranked_first(self):
        p = _portfolio()
        mgr = CapitalManager(p)

        candidates = [
            _candidate(opp_id=1, confidence=0.60, profit=50.0),
            _candidate(opp_id=2, confidence=0.95, profit=50.0),
            _candidate(opp_id=3, confidence=0.75, profit=50.0),
        ]
        decisions = mgr.evaluate_batch(candidates)
        approved_ids = [d.opportunity_id for d in decisions if d.approved]
        # opp 2 (highest confidence) should be first
        assert approved_ids[0] == 2

    def test_higher_profit_ranked_higher(self):
        p = _portfolio()
        mgr = CapitalManager(p)

        candidates = [
            _candidate(opp_id=1, profit=20.0, confidence=0.80),
            _candidate(opp_id=2, profit=100.0, confidence=0.80),
        ]
        decisions = mgr.evaluate_batch(candidates)
        approved = [d for d in decisions if d.approved]
        assert approved[0].opportunity_id == 2

    def test_lower_risk_ranked_higher(self):
        p = _portfolio()
        mgr = CapitalManager(p)

        candidates = [
            _candidate(opp_id=1, risk=80.0, profit=50.0, confidence=0.80),
            _candidate(opp_id=2, risk=10.0, profit=50.0, confidence=0.80),
        ]
        decisions = mgr.evaluate_batch(candidates)
        approved = [d for d in decisions if d.approved]
        assert approved[0].opportunity_id == 2

    def test_top_k_limits_execution(self):
        p = _portfolio()
        cfg = ManagerConfig(top_k=2)
        mgr = CapitalManager(p, manager_config=cfg)

        candidates = [
            _candidate(opp_id=i, product_id=i * 10, profit=50.0 - i)
            for i in range(5)
        ]
        decisions = mgr.evaluate_batch(candidates)
        approved = [d for d in decisions if d.approved]
        rejected_by_rank = [d for d in decisions if not d.approved and "ranked" in str(d.rejection_reasons)]
        assert len(approved) <= 2
        assert len(rejected_by_rank) >= 3


# ── Concurrent Trade Limits ──────────────────────────────────

class TestConcurrentLimits:

    def test_max_active_trades_enforced(self):
        p = _portfolio()
        cfg = ManagerConfig(max_active_trades=2, top_k=10)
        mgr = CapitalManager(p, manager_config=cfg)

        candidates = [
            _candidate(opp_id=i, product_id=i * 10) for i in range(5)
        ]
        decisions = mgr.evaluate_batch(candidates)
        approved = [d for d in decisions if d.approved]
        assert len(approved) <= 2


# ── Cooldown Tests ───────────────────────────────────────────

class TestCooldowns:

    def test_product_cooldown(self):
        p = _portfolio()
        mgr = CapitalManager(p)

        # First trade for product 100
        c1 = _candidate(opp_id=1, product_id=100)
        decisions1 = mgr.evaluate_batch([c1])
        assert decisions1[0].approved

        # Same product immediately — should be rejected
        c2 = _candidate(opp_id=2, product_id=100)
        decisions2 = mgr.evaluate_batch([c2])
        assert not decisions2[0].approved
        assert "cooldown" in decisions2[0].rejection_reasons[0]

    def test_different_product_no_cooldown(self):
        p = _portfolio()
        # Disable marketplace cooldown so only product cooldown is tested
        cfg = ManagerConfig(marketplace_cooldown_seconds=0)
        mgr = CapitalManager(p, manager_config=cfg)

        c1 = _candidate(opp_id=1, product_id=100)
        mgr.evaluate_batch([c1])

        c2 = _candidate(opp_id=2, product_id=200)
        decisions = mgr.evaluate_batch([c2])
        assert decisions[0].approved

    def test_marketplace_cooldown(self):
        p = _portfolio()
        cfg = ManagerConfig(marketplace_cooldown_seconds=300)
        mgr = CapitalManager(p, manager_config=cfg)

        c1 = _candidate(opp_id=1, product_id=100, sell_mp="mercadolibre_mx")
        mgr.evaluate_batch([c1])

        # Same sell marketplace — cooldown
        c2 = _candidate(opp_id=2, product_id=200, sell_mp="mercadolibre_mx")
        decisions = mgr.evaluate_batch([c2])
        assert not decisions[0].approved
        assert "marketplace" in decisions[0].rejection_reasons[0]

    def test_clear_cooldowns(self):
        p = _portfolio()
        mgr = CapitalManager(p)

        c1 = _candidate(opp_id=1, product_id=100)
        mgr.evaluate_batch([c1])

        mgr.clear_cooldowns()

        c2 = _candidate(opp_id=2, product_id=100)
        decisions = mgr.evaluate_batch([c2])
        assert decisions[0].approved


# ── Confidence Scaling ───────────────────────────────────────

class TestConfidenceScaling:

    def test_high_confidence_full_size(self):
        p = _portfolio()
        cfg = ManagerConfig(confidence_scaling=True, min_confidence_for_full_size=0.80)
        mgr = CapitalManager(p, manager_config=cfg)

        scale = mgr._confidence_scale(0.90)
        assert scale == 1.0

    def test_low_confidence_partial_size(self):
        p = _portfolio()
        cfg = ManagerConfig(confidence_scaling=True, min_confidence_for_full_size=0.80)
        mgr = CapitalManager(p, manager_config=cfg)

        scale = mgr._confidence_scale(0.40)
        assert 0.5 <= scale < 1.0

    def test_zero_confidence_floor(self):
        p = _portfolio()
        mgr = CapitalManager(p)
        scale = mgr._confidence_scale(0.0)
        assert scale == 0.5  # floor

    def test_scaling_disabled(self):
        p = _portfolio()
        cfg = ManagerConfig(confidence_scaling=False)
        mgr = CapitalManager(p, manager_config=cfg)

        # When disabled, capital_required is not scaled
        c = _candidate(opp_id=1, confidence=0.30, capital_required=200.0)
        decisions = mgr.evaluate_batch([c])
        # Should still work (guard evaluates unscaled)
        assert len(decisions) == 1


# ── Stop Conditions ──────────────────────────────────────────

class TestStopConditions:

    def test_drawdown_halts_trading(self):
        p = _portfolio(capital=10000.0)
        p.peak_capital = 12000.0
        p.total_capital = 9000.0
        p.drawdown_pct = 0.25  # 25%

        cfg = ManagerConfig(stop_max_drawdown_pct=0.15)
        mgr = CapitalManager(p, manager_config=cfg)

        decisions = mgr.evaluate_batch([_candidate()])
        assert not decisions[0].approved
        assert mgr.is_halted
        assert "drawdown" in decisions[0].rejection_reasons[0]

    def test_daily_loss_halts_trading(self):
        p = _portfolio(capital=10000.0)
        p.daily_loss_usd = 600.0  # > 5% of 10000

        cfg = ManagerConfig(stop_daily_loss_pct=0.05)
        mgr = CapitalManager(p, manager_config=cfg)

        decisions = mgr.evaluate_batch([_candidate()])
        assert not decisions[0].approved
        assert mgr.is_halted

    def test_low_success_rate_halts(self):
        p = _portfolio()
        p.total_trades = 20
        p.winning_trades = 5  # 25% win rate
        p.losing_trades = 15

        cfg = ManagerConfig(stop_min_success_rate=0.50, stop_min_trades_for_rate=10)
        mgr = CapitalManager(p, manager_config=cfg)

        decisions = mgr.evaluate_batch([_candidate()])
        assert not decisions[0].approved
        assert "win_rate" in decisions[0].rejection_reasons[0]

    def test_success_rate_ignored_below_min_trades(self):
        p = _portfolio()
        p.total_trades = 3
        p.winning_trades = 0  # 0% but only 3 trades

        cfg = ManagerConfig(stop_min_trades_for_rate=10)
        mgr = CapitalManager(p, manager_config=cfg)

        decisions = mgr.evaluate_batch([_candidate()])
        # Should NOT halt — not enough trades yet
        assert not mgr.is_halted

    def test_resume_after_halt(self):
        p = _portfolio()
        p.drawdown_pct = 0.20
        p.peak_capital = p.total_capital * 1.25

        cfg = ManagerConfig(stop_max_drawdown_pct=0.15)
        mgr = CapitalManager(p, manager_config=cfg)

        mgr.evaluate_batch([_candidate()])
        assert mgr.is_halted

        # Fix the drawdown and resume
        p.drawdown_pct = 0.05
        mgr.resume()
        assert not mgr.is_halted


# ── Dry-Run Mode ─────────────────────────────────────────────

class TestDryRun:

    def test_dry_run_does_not_allocate(self):
        p = _portfolio(capital=10000.0)
        mgr = CapitalManager(p)

        initial_available = p.available_capital
        decisions = mgr.evaluate_batch([_candidate()], dry_run=True)

        assert decisions[0].approved
        assert decisions[0].dry_run is True
        assert p.available_capital == initial_available  # unchanged
        assert p.open_position_count == 0

    def test_dry_run_does_not_set_cooldowns(self):
        p = _portfolio()
        mgr = CapitalManager(p)

        c1 = _candidate(opp_id=1, product_id=100)
        mgr.evaluate_batch([c1], dry_run=True)

        # Same product again — should not be in cooldown
        c2 = _candidate(opp_id=2, product_id=100)
        decisions = mgr.evaluate_batch([c2], dry_run=True)
        assert decisions[0].approved

    def test_dry_run_result_has_allocation(self):
        p = _portfolio()
        mgr = CapitalManager(p)
        decisions = mgr.evaluate_batch([_candidate()], dry_run=True)
        assert decisions[0].allocation > 0


# ── Capital Never Exceeded ───────────────────────────────────

class TestCapitalSafety:

    def test_cannot_overallocate(self):
        p = _portfolio(capital=100.0)
        cfg = ManagerConfig(top_k=10, max_active_trades=10)
        mgr = CapitalManager(p, manager_config=cfg)

        # Try to allocate more than available
        candidates = [
            _candidate(opp_id=i, product_id=i * 10, capital_required=80.0)
            for i in range(5)
        ]
        decisions = mgr.evaluate_batch(candidates)
        total_allocated = sum(d.allocation for d in decisions if d.approved)
        assert total_allocated <= 100.0

    def test_allocation_reduces_available(self):
        p = _portfolio(capital=10000.0)
        mgr = CapitalManager(p)

        decisions = mgr.evaluate_batch([_candidate(capital_required=200.0)])
        if decisions[0].approved:
            assert p.available_capital < 10000.0
            assert p.allocated_capital > 0


# ── Trade Lifecycle ──────────────────────────────────────────

class TestTradeLifecycle:

    def test_record_win(self):
        p = _portfolio(capital=10000.0)
        mgr = CapitalManager(p)

        mgr.evaluate_batch([_candidate(opp_id=1, capital_required=200.0)])
        mgr.record_win(1, actual_profit=50.0)

        assert p.winning_trades == 1
        assert p.realized_profit == 50.0
        assert p.total_capital > 10000.0

    def test_record_loss(self):
        p = _portfolio(capital=10000.0)
        mgr = CapitalManager(p)

        mgr.evaluate_batch([_candidate(opp_id=1, capital_required=200.0)])
        mgr.record_loss(1, loss_amount=30.0)

        assert p.losing_trades == 1
        assert p.realized_profit == -30.0
        assert p.total_capital < 10000.0

    def test_win_loss_sequence(self):
        p = _portfolio(capital=10000.0)
        cfg = ManagerConfig(product_cooldown_seconds=0, marketplace_cooldown_seconds=0)
        mgr = CapitalManager(p, manager_config=cfg)

        # Trade 1: win
        mgr.evaluate_batch([_candidate(opp_id=1, product_id=10)])
        mgr.record_win(1, 50.0)

        # Trade 2: loss
        mgr.evaluate_batch([_candidate(opp_id=2, product_id=20)])
        mgr.record_loss(2, 20.0)

        assert p.total_trades == 2
        assert p.winning_trades == 1
        assert p.losing_trades == 1
        assert p.realized_profit == 30.0  # 50 - 20


# ── Metrics ──────────────────────────────────────────────────

class TestMetrics:

    def test_initial_metrics(self):
        p = _portfolio(capital=10000.0)
        mgr = CapitalManager(p)
        m = mgr.get_metrics()

        assert m.total_capital == 10000.0
        assert m.available_capital == 10000.0
        assert m.utilization_pct == 0.0
        assert m.total_trades == 0
        assert not m.is_halted

    def test_metrics_after_trades(self):
        p = _portfolio(capital=10000.0)
        cfg = ManagerConfig(product_cooldown_seconds=0, marketplace_cooldown_seconds=0)
        mgr = CapitalManager(p, manager_config=cfg)

        mgr.evaluate_batch([_candidate(opp_id=1, product_id=10)])
        mgr.record_win(1, 50.0)
        mgr.evaluate_batch([_candidate(opp_id=2, product_id=20)])
        mgr.record_loss(2, 20.0)

        m = mgr.get_metrics()
        assert m.total_trades == 2
        assert m.winning_trades == 1
        assert m.losing_trades == 1
        assert m.win_rate == 50.0
        assert m.realized_profit == 30.0

    def test_halted_in_metrics(self):
        p = _portfolio()
        p.drawdown_pct = 0.20
        p.peak_capital = p.total_capital * 1.25
        cfg = ManagerConfig(stop_max_drawdown_pct=0.15)
        mgr = CapitalManager(p, manager_config=cfg)

        mgr.evaluate_batch([_candidate()])  # triggers halt check
        m = mgr.get_metrics()
        assert m.is_halted
        assert len(m.halt_reasons) > 0


# ── Edge Cases ───────────────────────────────────────────────

class TestEdgeCases:

    def test_empty_batch(self):
        p = _portfolio()
        mgr = CapitalManager(p)
        decisions = mgr.evaluate_batch([])
        assert decisions == []

    def test_single_candidate(self):
        p = _portfolio()
        mgr = CapitalManager(p)
        decisions = mgr.evaluate_batch([_candidate()])
        assert len(decisions) == 1

    def test_all_rejected_when_halted(self):
        p = _portfolio()
        p.drawdown_pct = 0.25
        p.peak_capital = p.total_capital * 1.33
        cfg = ManagerConfig(stop_max_drawdown_pct=0.15)
        mgr = CapitalManager(p, manager_config=cfg)

        candidates = [_candidate(opp_id=i, product_id=i * 10) for i in range(3)]
        decisions = mgr.evaluate_batch(candidates)
        assert all(not d.approved for d in decisions)
        assert all(len(d.rejection_reasons) > 0 for d in decisions)
