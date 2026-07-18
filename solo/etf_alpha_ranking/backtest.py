#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Walk-Forward Backtest
=====================
Simulates the single-ETF strategy on historical predictions and computes
performance metrics. Compares against:
  - CSI 300 buy & hold
  - ETF momentum baseline (top-1 by 60d return)

Metrics:
  CAGR, Annual Return, Win Rate, Avg Holding Days, Max Drawdown,
  Sharpe, Sortino, Turnover
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .portfolio import PortfolioEngine, Position

LOG = logging.getLogger("etf_alpha_ranking.backtest")


@dataclass
class BacktestMetrics:
    n_trades: int = 0
    win_rate: float = 0.0
    avg_holding_days: float = 0.0
    total_return: float = 0.0
    annual_return: float = 0.0
    cagr: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown: float = 0.0
    turnover: float = 0.0
    benchmark_return: float = 0.0
    benchmark_cagr: float = 0.0
    momentum_return: float = 0.0
    daily_returns: list = field(default_factory=list)
    trades: list = field(default_factory=list)


class WalkForwardBacktester:
    def __init__(self, config: dict):
        self.config = config
        bcfg = config.get("backtest", {})
        self.start_date = bcfg.get("start_date", "20240101")
        self.end_date = bcfg.get("end_date", "")
        self.commission = bcfg.get("commission", 0.0005)
        self.slippage = bcfg.get("slippage", 0.001)
        self.rebalance_freq = bcfg.get("rebalance_freq", 5)
        self.pf = PortfolioEngine(config)
        self.rf = config.get("general", {}).get("risk_free_rate", 0.03)

    # ------------------------------------------------------------------
    # Run backtest from a predictions panel + price data
    # ------------------------------------------------------------------
    def run(self, predictions: pd.DataFrame,
            etf_prices: Dict[str, pd.DataFrame],
            benchmark: pd.DataFrame) -> BacktestMetrics:
        """Run the walk-forward backtest.

        Args:
            predictions: long-format [date, etf, rank, prediction_score,
                         theme_persistence, leader_score, ...] for all dates
            etf_prices: {etf: DataFrame[trade_date, close, ...]}
            benchmark: DataFrame[trade_date, close] for CSI300
        Returns BacktestMetrics.
        """
        m = BacktestMetrics()
        if predictions.empty or benchmark.empty:
            return m
        pred = predictions.copy()
        pred["date"] = pred["date"].astype(str)
        dates = sorted(pred["date"].unique())
        if self.start_date:
            dates = [d for d in dates if d >= self.start_date]
        if self.end_date:
            dates = [d for d in dates if d <= self.end_date]
        if len(dates) < 5:
            return m

        bench = benchmark.sort_values("trade_date").copy()
        bench["trade_date"] = bench["trade_date"].astype(str)

        position: Optional[Position] = None
        daily_rets: List[float] = []
        prev_equity = 1.0
        # build a date -> next-date benchmark return map
        bench_ret = self._forward_returns(bench, 1)

        for i, d in enumerate(dates):
            day_pred = pred[pred["date"] == d].sort_values("rank")
            if day_pred.empty:
                daily_rets.append(0.0)
                continue
            top_row = day_pred.iloc[0]
            held_code = position.etf if position else None
            price_df = etf_prices.get(held_code) if held_code else None
            below_ma60 = self._is_below_ma60(price_df, d)

            sig = self.pf.manage(position, top_row.to_dict(), below_ma60)

            # execute signal
            if sig.signal == "BUY" and sig.etf:
                if held_code and held_code != sig.etf:
                    # rotate -> realize a trade
                    self._close_trade(position, d, etf_prices, m)
                entry_price = self._get_price(etf_prices.get(sig.etf), d, mode="close")
                position = Position(etf=sig.etf, entry_date=d, entry_price=entry_price,
                                    holding_days=0, rank=int(top_row.get("rank", 1)))
            elif sig.signal == "SELL" and position:
                self._close_trade(position, d, etf_prices, m)
                position = None
            elif position:
                position.holding_days += 1

            # daily P&L from the held position
            if position:
                px_today = self._get_price(etf_prices.get(position.etf), d, mode="close")
                px_prev = self._get_prev_price(etf_prices.get(position.etf), d)
                if px_prev > 0:
                    r = px_today / px_prev - 1.0
                else:
                    r = 0.0
            else:
                # flat -> earn benchmark return (cash proxy)
                r = bench_ret.get(d, 0.0)
            daily_rets.append(r)

        # close any open position at the end
        if position:
            self._close_trade(position, dates[-1], etf_prices, m)

        # ---- metrics ----
        m.daily_returns = daily_rets
        eq = np.cumprod([1.0 + r for r in daily_rets])
        m.total_return = float(eq[-1] - 1.0) if len(eq) else 0.0
        n_days = len(daily_rets)
        years = max(n_days / 252.0, 1e-6)
        m.annual_return = float((eq[-1]) ** (1.0 / years) - 1.0) if eq[-1] > 0 else -1.0
        m.cagr = m.annual_return
        # sharpe / sortino
        arr = np.array(daily_rets)
        if len(arr) > 2 and np.std(arr) > 1e-9:
            m.sharpe = float(np.mean(arr) / np.std(arr, ddof=1) * np.sqrt(252))
            downside = arr[arr < 0]
            if len(downside) > 0 and np.std(downside) > 1e-9:
                m.sortino = float(np.mean(arr) / np.std(downside, ddof=1) * np.sqrt(252))
        # max drawdown
        m.max_drawdown = self._max_dd(eq)
        # win rate / avg holding
        if m.trades:
            wins = [t for t in m.trades if t["return"] > 0]
            m.win_rate = len(wins) / len(m.trades)
            m.avg_holding_days = float(np.mean([t["holding_days"] for t in m.trades]))
        m.turnover = float(m.n_trades / max(years, 1e-6))

        # ---- baselines ----
        bench_seg = bench[(bench["trade_date"] >= dates[0]) & (bench["trade_date"] <= dates[-1])]
        if not bench_seg.empty:
            b0, b1 = float(bench_seg["close"].iloc[0]), float(bench_seg["close"].iloc[-1])
            m.benchmark_return = b1 / b0 - 1.0
            m.benchmark_cagr = float((b1 / b0) ** (1.0 / years) - 1.0) if b0 > 0 else 0.0
        m.momentum_return = self._momentum_baseline(pred, etf_prices, dates)
        return m

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _forward_returns(df: pd.DataFrame, horizon: int) -> Dict[str, float]:
        df = df.sort_values("trade_date").reset_index(drop=True)
        close = df["close"].values.astype(float)
        dates = df["trade_date"].values
        out = {}
        n = len(close)
        for i in range(n - horizon):
            out[str(dates[i])] = close[i + horizon] / close[i] - 1.0
        return out

    @staticmethod
    def _max_dd(equity: np.ndarray) -> float:
        if len(equity) == 0:
            return 0.0
        running_max = np.maximum.accumulate(equity)
        dd = (equity - running_max) / running_max
        return float(np.min(dd)) if len(dd) else 0.0

    @staticmethod
    def _get_price(df: Optional[pd.DataFrame], date: str, mode: str = "close") -> float:
        if df is None or df.empty:
            return 0.0
        df = df.sort_values("trade_date")
        df["trade_date"] = df["trade_date"].astype(str)
        row = df[df["trade_date"] <= date]
        if row.empty:
            return float(df["close"].iloc[0])
        return float(row[mode].iloc[-1])

    @staticmethod
    def _get_prev_price(df: Optional[pd.DataFrame], date: str) -> float:
        if df is None or df.empty:
            return 0.0
        df = df.sort_values("trade_date")
        df["trade_date"] = df["trade_date"].astype(str)
        rows = df[df["trade_date"] <= date]
        if len(rows) < 2:
            return 0.0
        return float(rows["close"].iloc[-2])

    @staticmethod
    def _is_below_ma60(df: Optional[pd.DataFrame], date: str) -> bool:
        if df is None or df.empty:
            return False
        df = df.sort_values("trade_date").reset_index(drop=True)
        df["trade_date"] = df["trade_date"].astype(str)
        idx = df.index[df["trade_date"] <= date]
        if len(idx) < 61:
            return False
        seg = df.loc[idx]
        close = seg["close"].values.astype(float)
        ma60 = pd.Series(close).rolling(60, min_periods=60).mean().values
        if not np.isfinite(ma60[-1]):
            return False
        return close[-1] < ma60[-1]

    def _close_trade(self, pos: Position, exit_date: str,
                     etf_prices: Dict[str, pd.DataFrame], m: BacktestMetrics):
        exit_px = self._get_price(etf_prices.get(pos.etf), exit_date)
        if pos.entry_price <= 0:
            return
        ret = exit_px / pos.entry_price - 1.0
        # transaction cost
        ret -= 2 * (self.commission + self.slippage)
        m.trades.append({
            "etf": pos.etf, "entry_date": pos.entry_date, "exit_date": exit_date,
            "entry_price": pos.entry_price, "exit_price": exit_px,
            "return": ret, "holding_days": pos.holding_days,
        })
        m.n_trades += 1

    def _momentum_baseline(self, pred: pd.DataFrame,
                           etf_prices: Dict[str, pd.DataFrame],
                           dates: List[str]) -> float:
        """Top-1 by 60d momentum held across the whole window."""
        if not dates or not etf_prices:
            return 0.0
        # pick the ETF with the strongest 60d return at the start date
        start = dates[0]
        best, best_ret = "", -1e9
        for code, df in etf_prices.items():
            if df is None or df.empty:
                continue
            df = df.sort_values("trade_date")
            df["trade_date"] = df["trade_date"].astype(str)
            seg = df[df["trade_date"] <= start]
            if len(seg) < 61:
                continue
            close = seg["close"].values.astype(float)
            r = close[-1] / close[-61] - 1.0
            if r > best_ret:
                best_ret, best = r, code
        if not best:
            return 0.0
        df = etf_prices[best].sort_values("trade_date")
        df["trade_date"] = df["trade_date"].astype(str)
        seg = df[(df["trade_date"] >= start) & (df["trade_date"] <= dates[-1])]
        if seg.empty:
            return 0.0
        return float(seg["close"].iloc[-1] / seg["close"].iloc[0] - 1.0)

    @staticmethod
    def to_dict(m: BacktestMetrics) -> dict:
        return {
            "n_trades": m.n_trades,
            "win_rate": round(m.win_rate, 4),
            "avg_holding_days": round(m.avg_holding_days, 1),
            "total_return": round(m.total_return, 4),
            "annual_return": round(m.annual_return, 4),
            "cagr": round(m.cagr, 4),
            "sharpe": round(m.sharpe, 3),
            "sortino": round(m.sortino, 3),
            "max_drawdown": round(m.max_drawdown, 4),
            "turnover": round(m.turnover, 2),
            "benchmark_return": round(m.benchmark_return, 4),
            "benchmark_cagr": round(m.benchmark_cagr, 4),
            "momentum_return": round(m.momentum_return, 4),
        }
