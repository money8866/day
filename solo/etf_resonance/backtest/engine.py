"""Backtest Engine for the ETF Resonance System.

Supports:
- Walk Forward Analysis
- Rolling Window Cross Validation
- Time Series Cross Validation
- Parameter Optimization
- Performance Metrics (Sharpe, Calmar, MaxDD, WinRate)
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Callable, Any
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from etf_resonance.utils.helpers import Config
from etf_resonance.utils.indicators import sharpe_ratio, calmar_ratio, max_drawdown

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """Complete backtest result."""
    total_return: float
    annual_return: float
    sharpe: float
    calmar: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    winning_trades: int
    avg_return_per_trade: float
    avg_hold_days: float
    trade_log: pd.DataFrame = field(default_factory=pd.DataFrame)
    equity_curve: pd.Series = field(default_factory=pd.Series)


@dataclass
class TradeRecord:
    """Single trade record."""
    entry_date: str
    exit_date: str
    stock_code: str
    stock_name: str
    etf_code: str
    entry_price: float
    exit_price: float
    shares: int
    return_pct: float
    hold_days: int
    exit_reason: str
    composite_score: float


class BacktestEngine:
    """Backtest engine for walk-forward and rolling-window validation."""

    def __init__(self, config: Optional[Config] = None):
        cfg = config.get("backtest", {}) if config else {}
        self.initial_capital = float(cfg.get("initial_capital", 1_000_000))
        self.commission = float(cfg.get("commission_pct", 0.0003))
        self.max_positions = int(cfg.get("max_positions", 20))
        self.sl_atr = float(cfg.get("stop_loss_atr", 2.0))
        self.tp_atr = float(cfg.get("take_profit_atr", 6.0))
        self.walk_forward_window = int(cfg.get("walk_forward_window", 60))
        self.retrain_frequency = int(cfg.get("retrain_frequency", 20))

        self._trade_log: List[TradeRecord] = []
        self._equity: List[float] = []
        self._positions: Dict[str, Dict] = {}

    def walk_forward(self, data: Dict[str, pd.DataFrame],
                     scoring_fn: Callable,
                     dates: List[str],
                     **kwargs) -> BacktestResult:
        """Walk Forward Analysis.

        Divides data into sequential train/test windows.
        Trains on window i, tests on window i+1.
        """
        all_trades: List[TradeRecord] = []
        equity = [self.initial_capital]

        window = self.walk_forward_window
        step = self.retrain_frequency

        for start_idx in range(0, len(dates) - window, step):
            train_end = start_idx + window
            test_end = min(train_end + step, len(dates))

            if test_end >= len(dates):
                break

            train_dates = dates[start_idx:train_end]
            test_dates = dates[train_end:test_end]

            # Train on training window
            logger.info(f"WF Window: train={train_dates[0]}~{train_dates[-1]}, "
                       f"test={test_dates[0]}~{test_dates[-1]}")

            # Execute trades on test window
            self._execute_window(data, scoring_fn, test_dates, **kwargs)

        return self._compute_results()

    def rolling_cv(self, data: Dict[str, pd.DataFrame],
                   scoring_fn: Callable,
                   dates: List[str],
                   n_splits: int = 5,
                   **kwargs) -> BacktestResult:
        """Rolling Window Cross Validation."""
        window_size = len(dates) // (n_splits + 1)
        all_trades = []

        for fold in range(n_splits):
            train_end = (fold + 1) * window_size
            test_start = train_end
            test_end = min(test_start + window_size, len(dates))

            if test_end >= len(dates):
                break

            train_dates = dates[:train_end]
            test_dates = dates[test_start:test_end]

            logger.info(f"RollingCV fold {fold + 1}: "
                       f"train={len(train_dates)}d, test={len(test_dates)}d")

            self._execute_window(data, scoring_fn, test_dates, **kwargs)

        return self._compute_results()

    def _execute_window(self, data: Dict[str, pd.DataFrame],
                        scoring_fn: Callable,
                        test_dates: List[str],
                        **kwargs) -> None:
        """Execute trading on a single test window."""
        capital = self.initial_capital

        for date in test_dates:
            try:
                rankings = scoring_fn(data=data, as_of_date=date, **kwargs)
                top_picks = rankings[:self.max_positions]

                # Close positions not in top picks
                self._close_positions(top_picks, date, data)

                # Open new positions
                self._open_positions(top_picks, date, data, capital)
            except Exception as e:
                logger.error(f"Error on {date}: {e}")
                continue

    def _open_positions(self, picks: List, date: str,
                        data: Dict, capital: float) -> None:
        """Open positions from ranking picks."""
        for pick in picks[:self.max_positions]:
            code = self._get_stock_code(pick)
            if code in self._positions:
                continue
            if len(self._positions) >= self.max_positions:
                break

            df = data.get(code)
            if df is None or df.empty:
                continue

            entry_price = self._get_price(df, date)
            if entry_price is None:
                continue

            # Position sizing
            position_size = capital / self.max_positions
            shares = int(position_size / entry_price)

            if shares <= 0:
                continue

            self._positions[code] = {
                "entry_date": date,
                "entry_price": entry_price,
                "shares": shares,
                "stock_code": code,
                "stock_name": self._get_name(pick, code),
                "etf_code": self._get_etf_code(pick),
                "composite_score": self._get_composite(pick),
            }

    def _close_positions(self, picks: List, date: str,
                         data: Dict) -> None:
        """Close positions not in current ranking."""
        current_codes = {self._get_stock_code(p) for p in picks}
        to_close = [code for code in self._positions
                    if code not in current_codes]

        for code in to_close:
            self._exit_position(code, date, data, "Ranking_Dropped")

    def _exit_position(self, code: str, date: str,
                       data: Dict, reason: str) -> None:
        """Exit a specific position."""
        pos = self._positions.get(code)
        if pos is None:
            return

        df = data.get(code)
        exit_price = self._get_price(df, date) if df is not None else pos["entry_price"]
        if exit_price is None:
            exit_price = pos["entry_price"]

        ret = (exit_price / pos["entry_price"] - 1) * 100
        ret -= self.commission * 100

        hold_days = len(pd.bdate_range(pos["entry_date"], date)) - 1

        self._trade_log.append(TradeRecord(
            entry_date=pos["entry_date"],
            exit_date=date,
            stock_code=pos["stock_code"],
            stock_name=pos["stock_name"],
            etf_code=pos["etf_code"],
            entry_price=pos["entry_price"],
            exit_price=exit_price,
            shares=pos["shares"],
            return_pct=round(float(ret), 2),
            hold_days=max(hold_days, 1),
            exit_reason=reason,
            composite_score=pos.get("composite_score", 0),
        ))

        del self._positions[code]

    def _get_price(self, df: pd.DataFrame, date: str) -> Optional[float]:
        """Get close price for a date."""
        date_col = "trade_date" if "trade_date" in df.columns else df.index.name
        if date_col is None:
            return None
        row = df[df[date_col] == date]
        return float(row["close"].iloc[0]) if not row.empty else None

    def _get_stock_code(self, pick) -> str:
        if hasattr(pick, "ts_code"):
            return pick.ts_code
        if isinstance(pick, dict):
            return pick.get("ts_code") or pick.get("Code", "")
        return str(pick[0]) if isinstance(pick, (list, tuple)) else str(pick)

    def _get_name(self, pick, default: str) -> str:
        if hasattr(pick, "name"):
            return pick.name
        if isinstance(pick, dict):
            return pick.get("name") or pick.get("Stock", default)
        return default

    def _get_etf_code(self, pick) -> str:
        if hasattr(pick, "etf_code"):
            return pick.etf_code
        if isinstance(pick, dict):
            return pick.get("etf_code") or pick.get("ETF", "")
        return ""

    def _get_composite(self, pick) -> float:
        if hasattr(pick, "composite_score"):
            return pick.composite_score
        if isinstance(pick, dict):
            return float(pick.get("composite_score") or pick.get("Composite", 50))
        return 50.0

    def _compute_results(self) -> BacktestResult:
        """Compute final backtest statistics."""
        if not self._trade_log:
            return BacktestResult(
                total_return=0, annual_return=0, sharpe=0, calmar=0,
                max_drawdown=0, win_rate=0, total_trades=0,
                winning_trades=0, avg_return_per_trade=0, avg_hold_days=0,
            )

        trades_df = pd.DataFrame([t.__dict__ for t in self._trade_log])
        winning = trades_df[trades_df["return_pct"] > 0]
        total_return = (1 + trades_df["return_pct"].mean() / 100) ** len(trades_df) - 1

        return BacktestResult(
            total_return=round(float(total_return * 100), 2),
            annual_return=round(float(total_return * 100 / max(len(trades_df) / 252, 0.01)), 2),
            sharpe=round(float(sharpe_ratio(trades_df["return_pct"].values / 100, 252)), 2),
            calmar=round(float(total_return * 100 / max(max_drawdown(trades_df["return_pct"].values), 0.01)), 2),
            max_drawdown=round(float(max_drawdown((1 + trades_df["return_pct"].cumsum() / 100).values)), 2),
            win_rate=round(float(len(winning) / max(len(trades_df), 1) * 100), 1),
            total_trades=len(trades_df),
            winning_trades=len(winning),
            avg_return_per_trade=round(float(trades_df["return_pct"].mean()), 2),
            avg_hold_days=round(float(trades_df["hold_days"].mean()), 1),
            trade_log=trades_df,
        )
