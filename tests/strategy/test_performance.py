"""
Tests for PerformanceTracker
"""

import pytest
import pandas as pd
from src.strategy.performance import PerformanceTracker, StrategyPerformance


def test_performance_tracker_initialization():
    """Test PerformanceTracker initialization."""
    tracker = PerformanceTracker("TestStrategy")

    assert tracker.strategy_name == "TestStrategy"
    assert len(tracker.pnl_history) == 0
    assert len(tracker.trade_history) == 0


def test_record_trade():
    """Test recording a trade."""
    tracker = PerformanceTracker("TestStrategy")

    entry_time = pd.Timestamp('2024-01-01 09:00:00')
    exit_time = pd.Timestamp('2024-01-01 10:00:00')

    tracker.record_trade(
        symbol='AAPL',
        entry_price=150.0,
        exit_price=155.0,
        quantity=100,
        entry_time=entry_time,
        exit_time=exit_time,
        pnl=500.0
    )

    assert len(tracker.trade_history) == 1
    trade = tracker.trade_history[0]
    assert trade['symbol'] == 'AAPL'
    assert trade['pnl'] == 500.0
    assert trade['hold_time_hours'] == 1.0


def test_calculate_performance_no_trades():
    """Test performance calculation with no trades."""
    tracker = PerformanceTracker("TestStrategy")

    performance = tracker.calculate_performance()

    assert isinstance(performance, StrategyPerformance)
    assert performance.total_pnl == 0.0
    assert performance.num_trades == 0
    assert performance.win_rate == 0.0


def test_calculate_performance_with_trades():
    """Test performance calculation with trades."""
    tracker = PerformanceTracker("TestStrategy")

    # Record winning trades
    tracker.record_trade(
        symbol='AAPL',
        entry_price=150.0,
        exit_price=155.0,
        quantity=100,
        entry_time=pd.Timestamp('2024-01-01 09:00:00'),
        exit_time=pd.Timestamp('2024-01-01 10:00:00'),
        pnl=500.0
    )

    tracker.record_trade(
        symbol='MSFT',
        entry_price=300.0,
        exit_price=310.0,
        quantity=50,
        entry_time=pd.Timestamp('2024-01-02 09:00:00'),
        exit_time=pd.Timestamp('2024-01-02 11:00:00'),
        pnl=500.0
    )

    # Record losing trade
    tracker.record_trade(
        symbol='GOOGL',
        entry_price=100.0,
        exit_price=95.0,
        quantity=100,
        entry_time=pd.Timestamp('2024-01-03 09:00:00'),
        exit_time=pd.Timestamp('2024-01-03 15:00:00'),
        pnl=-500.0
    )

    performance = tracker.calculate_performance()

    assert performance.num_trades == 3
    assert performance.total_pnl == 500.0  # 500 + 500 - 500
    assert performance.win_rate == 2/3  # 2 winning trades out of 3
    assert performance.avg_hold_time > 0


def test_calculate_performance_all_winning():
    """Test performance with all winning trades."""
    tracker = PerformanceTracker("TestStrategy")

    for i in range(5):
        tracker.record_trade(
            symbol='AAPL',
            entry_price=150.0,
            exit_price=155.0,
            quantity=100,
            entry_time=pd.Timestamp('2024-01-01 09:00:00'),
            exit_time=pd.Timestamp('2024-01-01 10:00:00'),
            pnl=500.0
        )

    performance = tracker.calculate_performance()

    assert performance.num_trades == 5
    assert performance.win_rate == 1.0  # 100% win rate
    assert performance.total_pnl == 2500.0


def test_calculate_performance_sharpe_ratio():
    """Test Sharpe ratio calculation."""
    tracker = PerformanceTracker("TestStrategy")

    # Record trades with varying P&L
    pnls = [100, -50, 150, -30, 200]
    for pnl in pnls:
        tracker.record_trade(
            symbol='AAPL',
            entry_price=150.0,
            exit_price=155.0,
            quantity=100,
            entry_time=pd.Timestamp('2024-01-01 09:00:00'),
            exit_time=pd.Timestamp('2024-01-01 10:00:00'),
            pnl=pnl
        )

    performance = tracker.calculate_performance()

    assert performance.sharpe_ratio != 0.0  # Should calculate something
    assert performance.num_trades == 5
