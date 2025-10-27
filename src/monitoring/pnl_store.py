"""
P&L Time-Series Storage Layer

Stores portfolio P&L snapshots for historical tracking and charting.
Provides intraday and daily P&L data for dashboard visualization.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple


class PnLStore:
    """Persistent storage for portfolio P&L time series."""

    def __init__(self, db_path: str = "logs/pnl.db"):
        """Initialize P&L store with SQLite database.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            # Portfolio-level P&L snapshots
            conn.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_pnl (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    portfolio_value REAL NOT NULL,
                    high_water_mark REAL NOT NULL,
                    drawdown_pct REAL NOT NULL,
                    daily_pnl REAL,
                    daily_return_pct REAL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Strategy-level P&L snapshots
            conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_pnl (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    strategy_value REAL NOT NULL,
                    daily_pnl REAL,
                    daily_return_pct REAL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Risk metrics snapshots
            conn.execute("""
                CREATE TABLE IF NOT EXISTS risk_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    var_95 REAL,
                    cvar_95 REAL,
                    sharpe_30d REAL,
                    sharpe_90d REAL,
                    sharpe_365d REAL,
                    beta REAL,
                    volatility REAL,
                    max_drawdown REAL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indices
            conn.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_timestamp ON portfolio_pnl(timestamp DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_timestamp ON strategy_pnl(timestamp DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_id ON strategy_pnl(strategy_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_risk_timestamp ON risk_metrics(timestamp DESC)")

            conn.commit()

    def store_portfolio_snapshot(
        self,
        portfolio_value: float,
        high_water_mark: float,
        drawdown_pct: float,
        daily_pnl: Optional[float] = None,
        daily_return_pct: Optional[float] = None,
        timestamp: Optional[datetime] = None
    ) -> int:
        """Store portfolio P&L snapshot.

        Args:
            portfolio_value: Current portfolio value
            high_water_mark: Current high-water mark
            drawdown_pct: Current drawdown percentage
            daily_pnl: Daily P&L change
            daily_return_pct: Daily return percentage
            timestamp: Snapshot timestamp (defaults to now)

        Returns:
            Snapshot ID from database
        """
        if timestamp is None:
            timestamp = datetime.now()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO portfolio_pnl (timestamp, portfolio_value, high_water_mark, drawdown_pct, daily_pnl, daily_return_pct)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (timestamp.isoformat(), portfolio_value, high_water_mark, drawdown_pct, daily_pnl, daily_return_pct)
            )
            conn.commit()
            return cursor.lastrowid

    def store_strategy_snapshot(
        self,
        strategy_id: str,
        strategy_value: float,
        daily_pnl: Optional[float] = None,
        daily_return_pct: Optional[float] = None,
        timestamp: Optional[datetime] = None
    ) -> int:
        """Store strategy P&L snapshot.

        Args:
            strategy_id: Strategy identifier
            strategy_value: Current strategy value
            daily_pnl: Daily P&L change
            daily_return_pct: Daily return percentage
            timestamp: Snapshot timestamp (defaults to now)

        Returns:
            Snapshot ID from database
        """
        if timestamp is None:
            timestamp = datetime.now()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO strategy_pnl (timestamp, strategy_id, strategy_value, daily_pnl, daily_return_pct)
                VALUES (?, ?, ?, ?, ?)
                """,
                (timestamp.isoformat(), strategy_id, strategy_value, daily_pnl, daily_return_pct)
            )
            conn.commit()
            return cursor.lastrowid

    def store_risk_metrics(
        self,
        var_95: Optional[float] = None,
        cvar_95: Optional[float] = None,
        sharpe_30d: Optional[float] = None,
        sharpe_90d: Optional[float] = None,
        sharpe_365d: Optional[float] = None,
        beta: Optional[float] = None,
        volatility: Optional[float] = None,
        max_drawdown: Optional[float] = None,
        timestamp: Optional[datetime] = None
    ) -> int:
        """Store risk metrics snapshot.

        Args:
            var_95: Value at Risk (95% confidence)
            cvar_95: Conditional VaR (95% confidence)
            sharpe_30d: 30-day Sharpe ratio
            sharpe_90d: 90-day Sharpe ratio
            sharpe_365d: 365-day Sharpe ratio
            beta: Portfolio beta vs benchmark
            volatility: Annualized volatility
            max_drawdown: Maximum drawdown
            timestamp: Snapshot timestamp (defaults to now)

        Returns:
            Snapshot ID from database
        """
        if timestamp is None:
            timestamp = datetime.now()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO risk_metrics (timestamp, var_95, cvar_95, sharpe_30d, sharpe_90d, sharpe_365d, beta, volatility, max_drawdown)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (timestamp.isoformat(), var_95, cvar_95, sharpe_30d, sharpe_90d, sharpe_365d, beta, volatility, max_drawdown)
            )
            conn.commit()
            return cursor.lastrowid

    def get_portfolio_pnl_history(self, hours: int = 24) -> List[Dict]:
        """Get portfolio P&L history for last N hours.

        Args:
            hours: Time window in hours

        Returns:
            List of P&L snapshots with timestamp, value, HWM, drawdown
        """
        cutoff = datetime.now() - timedelta(hours=hours)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT timestamp, portfolio_value, high_water_mark, drawdown_pct, daily_pnl, daily_return_pct
                FROM portfolio_pnl
                WHERE timestamp >= ?
                ORDER BY timestamp ASC
                """,
                (cutoff.isoformat(),)
            )
            rows = cursor.fetchall()

        return [
            {
                "timestamp": timestamp,
                "portfolio_value": value,
                "high_water_mark": hwm,
                "drawdown_pct": drawdown,
                "daily_pnl": daily_pnl,
                "daily_return_pct": daily_return
            }
            for timestamp, value, hwm, drawdown, daily_pnl, daily_return in rows
        ]

    def get_strategy_pnl_history(self, strategy_id: str, hours: int = 24) -> List[Dict]:
        """Get strategy P&L history for last N hours.

        Args:
            strategy_id: Strategy identifier
            hours: Time window in hours

        Returns:
            List of strategy P&L snapshots
        """
        cutoff = datetime.now() - timedelta(hours=hours)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT timestamp, strategy_value, daily_pnl, daily_return_pct
                FROM strategy_pnl
                WHERE strategy_id = ? AND timestamp >= ?
                ORDER BY timestamp ASC
                """,
                (strategy_id, cutoff.isoformat())
            )
            rows = cursor.fetchall()

        return [
            {
                "timestamp": timestamp,
                "strategy_value": value,
                "daily_pnl": daily_pnl,
                "daily_return_pct": daily_return
            }
            for timestamp, value, daily_pnl, daily_return in rows
        ]

    def get_risk_metrics_history(self, hours: int = 24) -> List[Dict]:
        """Get risk metrics history for last N hours.

        Args:
            hours: Time window in hours

        Returns:
            List of risk metrics snapshots
        """
        cutoff = datetime.now() - timedelta(hours=hours)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT timestamp, var_95, cvar_95, sharpe_30d, sharpe_90d, sharpe_365d, beta, volatility, max_drawdown
                FROM risk_metrics
                WHERE timestamp >= ?
                ORDER BY timestamp ASC
                """,
                (cutoff.isoformat(),)
            )
            rows = cursor.fetchall()

        return [
            {
                "timestamp": timestamp,
                "var_95": var_95,
                "cvar_95": cvar_95,
                "sharpe_30d": sharpe_30d,
                "sharpe_90d": sharpe_90d,
                "sharpe_365d": sharpe_365d,
                "beta": beta,
                "volatility": volatility,
                "max_drawdown": max_drawdown
            }
            for timestamp, var_95, cvar_95, sharpe_30d, sharpe_90d, sharpe_365d, beta, volatility, max_drawdown in rows
        ]

    def get_latest_portfolio_value(self) -> Optional[float]:
        """Get most recent portfolio value.

        Returns:
            Latest portfolio value or None if no data
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT portfolio_value FROM portfolio_pnl ORDER BY timestamp DESC LIMIT 1"
            )
            row = cursor.fetchone()
            return row[0] if row else None

    def purge_old_snapshots(self, days: int = 30) -> Tuple[int, int, int]:
        """Delete snapshots older than N days.

        Args:
            days: Age threshold in days

        Returns:
            Tuple of (portfolio_count, strategy_count, metrics_count) deleted
        """
        cutoff = datetime.now() - timedelta(days=days)

        with sqlite3.connect(self.db_path) as conn:
            cursor1 = conn.execute(
                "DELETE FROM portfolio_pnl WHERE timestamp < ?",
                (cutoff.isoformat(),)
            )
            portfolio_count = cursor1.rowcount

            cursor2 = conn.execute(
                "DELETE FROM strategy_pnl WHERE timestamp < ?",
                (cutoff.isoformat(),)
            )
            strategy_count = cursor2.rowcount

            cursor3 = conn.execute(
                "DELETE FROM risk_metrics WHERE timestamp < ?",
                (cutoff.isoformat(),)
            )
            metrics_count = cursor3.rowcount

            conn.commit()

        return portfolio_count, strategy_count, metrics_count
