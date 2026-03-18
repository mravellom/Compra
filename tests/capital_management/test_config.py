"""
Tests — Capital Config: strategy presets, bounds, get_config.
"""
import pytest

from capital_management.config import (
    CapitalConfig,
    StrategyMode,
    get_config,
    STRATEGY_PRESETS,
)


class TestStrategyPresets:

    def test_all_modes_defined(self):
        for mode in StrategyMode:
            assert mode in STRATEGY_PRESETS

    def test_conservative_is_strictest(self):
        c = STRATEGY_PRESETS[StrategyMode.CONSERVATIVE]
        b = STRATEGY_PRESETS[StrategyMode.BALANCED]
        a = STRATEGY_PRESETS[StrategyMode.AGGRESSIVE]

        # Position sizing: conservative < balanced < aggressive
        assert c.position_sizing.max_allocation_pct_per_trade < b.position_sizing.max_allocation_pct_per_trade
        assert b.position_sizing.max_allocation_pct_per_trade < a.position_sizing.max_allocation_pct_per_trade

        # Risk: conservative tighter drawdown limits
        assert c.risk.max_drawdown_pct < b.risk.max_drawdown_pct
        assert b.risk.max_drawdown_pct < a.risk.max_drawdown_pct

        # Consecutive loss limit: conservative reacts faster
        assert c.risk.consecutive_loss_limit < b.risk.consecutive_loss_limit
        assert b.risk.consecutive_loss_limit < a.risk.consecutive_loss_limit

        # Max positions: conservative fewer
        assert c.diversification.max_open_positions < b.diversification.max_open_positions
        assert b.diversification.max_open_positions < a.diversification.max_open_positions

    def test_min_allocation_conservative_highest(self):
        c = STRATEGY_PRESETS[StrategyMode.CONSERVATIVE]
        a = STRATEGY_PRESETS[StrategyMode.AGGRESSIVE]
        assert c.position_sizing.min_allocation_usd > a.position_sizing.min_allocation_usd


class TestGetConfig:

    def test_default_is_balanced(self):
        cfg = get_config()
        assert cfg.strategy == StrategyMode.BALANCED

    def test_string_mode(self):
        cfg = get_config("conservative")
        assert cfg.strategy == StrategyMode.CONSERVATIVE

    def test_enum_mode(self):
        cfg = get_config(StrategyMode.AGGRESSIVE)
        assert cfg.strategy == StrategyMode.AGGRESSIVE

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            get_config("yolo")

    def test_all_configs_have_positive_values(self):
        for mode in StrategyMode:
            cfg = STRATEGY_PRESETS[mode]
            assert cfg.position_sizing.max_allocation_pct_per_trade > 0
            assert cfg.risk.max_drawdown_pct > 0
            assert cfg.diversification.max_open_positions > 0
            assert cfg.risk.max_daily_loss_usd > 0
