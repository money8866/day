"""回测性能指标计算模块 — 全向量化实现。"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Optional, Tuple
from loguru import logger


_EPS = 1e-10


@dataclass
class BacktestMetrics:
    """回测综合绩效指标。"""
    total_trades: int = 0
    win_trades: int = 0
    loss_trades: int = 0
    win_rate: float = 0.0
    total_return_pct: float = 0.0
    annual_return: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    max_win_pct: float = 0.0
    max_loss_pct: float = 0.0
    avg_hold_days: float = 0.0
    total_fees: float = 0.0
    start_date: str = ""
    end_date: str = ""


def compute_metrics(equity_curve: np.ndarray,
                    trades: List[dict],
                    initial_capital: float = 1_000_000,
                    periods_per_year: int = 252,
                    risk_free_rate: float = 0.02) -> BacktestMetrics:
    """从权益曲线和交易列表计算全面的回测指标。

    Parameters
    ----------
    equity_curve : np.ndarray
        每日 NAV 数组（长度 = 回测天数 + 1，首日为初始资金）。
    trades : list[dict]
        交易记录列表，每条记录需含 pnl_pct, exit_date, entry_date 等字段。
    initial_capital : float
        初始资金。
    periods_per_year : int
        年化周期数，日频数据默认 252。
    risk_free_rate : float
        无风险利率，默认 0.02。

    Returns
    -------
    BacktestMetrics
    """
    metrics = BacktestMetrics()
    n_trades = len(trades)

    if n_trades == 0 or len(equity_curve) < 2:
        logger.warning("No trades or insufficient equity curve data")
        metrics.total_trades = n_trades
        return metrics

    metrics.total_trades = n_trades

    # 区间日期
    if trades:
        try:
            metrics.start_date = str(trades[0].get('entry_date', ''))
            metrics.end_date = str(trades[-1].get('exit_date', ''))
        except Exception:
            pass

    # --- 从权益曲线计算收益率序列 ---
    ec = np.asarray(equity_curve, dtype=np.float64)
    returns = np.diff(ec) / np.maximum(ec[:-1], _EPS)

    # 总收益率
    metrics.total_return_pct = float((ec[-1] / max(ec[0], _EPS) - 1.0) * 100.0)

    # 年化收益率
    metrics.annual_return = compute_annual_return(equity_curve, periods_per_year)

    # 最大回撤
    mdd, peak_idx, trough_idx = compute_max_drawdown(ec)
    metrics.max_drawdown_pct = float(mdd * 100.0)

    # Sharpe
    metrics.sharpe_ratio = compute_sharpe(returns, periods_per_year, risk_free_rate)

    # Sortino
    metrics.sortino_ratio = compute_sortino(returns, periods_per_year, risk_free_rate)

    # Calmar
    metrics.calmar_ratio = compute_calmar(ec, periods_per_year)

    # --- 从交易列表计算指标 ---
    pnl_pcts = np.array([t.get('pnl_pct', 0.0) for t in trades], dtype=np.float64)
    gross_fees = np.array([t.get('fee', 0.0) for t in trades], dtype=np.float64)
    hold_days_list = []
    for t in trades:
        ed = t.get('entry_date', '')
        xd = t.get('exit_date', '')
        if ed and xd and isinstance(ed, str) and isinstance(xd, str):
            try:
                delta = (pd.Timestamp(xd) - pd.Timestamp(ed)).days
                hold_days_list.append(max(delta, 0))
            except Exception:
                hold_days_list.append(0)
        else:
            hold_days_list.append(0)
    hold_days = np.array(hold_days_list, dtype=np.float64)

    wins = pnl_pcts > _EPS
    losses = pnl_pcts <= -_EPS
    n_wins = int(wins.sum())
    n_losses = int(losses.sum())

    metrics.win_trades = n_wins
    metrics.loss_trades = n_losses
    metrics.win_rate = float(n_wins / max(n_trades, 1)) * 100.0

    # Profit Factor
    metrics.profit_factor = compute_profit_factor(trades)

    # Expectancy
    metrics.expectancy = compute_expectancy(trades)

    # 平均盈亏
    if n_wins > 0:
        metrics.avg_win_pct = float(np.mean(pnl_pcts[wins])) * 100.0
        metrics.max_win_pct = float(np.max(pnl_pcts[wins])) * 100.0
    if n_losses > 0:
        metrics.avg_loss_pct = float(np.mean(pnl_pcts[losses])) * 100.0
        metrics.max_loss_pct = float(np.min(pnl_pcts[losses])) * 100.0

    # 平均持仓天数
    metrics.avg_hold_days = float(np.mean(hold_days)) if len(hold_days) > 0 else 0.0

    # 总手续费
    metrics.total_fees = float(np.sum(gross_fees))

    logger.info(
        f"Metrics: {n_trades} trades, WR={metrics.win_rate:.1f}%, "
        f"Ret={metrics.total_return_pct:.2f}%, "
        f"Sharpe={metrics.sharpe_ratio:.2f}, "
        f"MDD={metrics.max_drawdown_pct:.2f}%"
    )
    return metrics


def compute_sharpe(returns: np.ndarray,
                   periods_per_year: int = 252,
                   risk_free_rate: float = 0.02) -> float:
    """年化 Sharpe 比率。

    Sharpe = (E[R] - Rf) / sigma(R) * sqrt(periods)
    """
    r = np.asarray(returns, dtype=np.float64)
    valid = np.isfinite(r)
    n = int(valid.sum())
    if n < 5:
        return 0.0
    r_valid = r[valid]
    rf_period = risk_free_rate / max(periods_per_year, 1)
    excess = r_valid - rf_period
    std = float(np.std(excess, ddof=1))
    if std < _EPS:
        return 0.0
    return float(np.mean(excess) / std * np.sqrt(periods_per_year))


def compute_sortino(returns: np.ndarray,
                    periods_per_year: int = 252,
                    risk_free_rate: float = 0.02) -> float:
    """Sortino 比率（仅用下行波动率）。"""
    r = np.asarray(returns, dtype=np.float64)
    valid = np.isfinite(r)
    n = int(valid.sum())
    if n < 5:
        return 0.0
    r_valid = r[valid]
    rf_period = risk_free_rate / max(periods_per_year, 1)
    excess = r_valid - rf_period
    downside = excess[excess < 0]
    downside_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else 0.0
    if downside_std < _EPS:
        return 0.0
    return float(np.mean(excess) / downside_std * np.sqrt(periods_per_year))


def compute_calmar(equity_curve: np.ndarray,
                   periods_per_year: int = 252) -> float:
    """Calmar 比率 = 年化收益率 / 最大回撤绝对值。"""
    ann_ret = compute_annual_return(equity_curve, periods_per_year)
    mdd, _, _ = compute_max_drawdown(equity_curve)
    if abs(mdd) < _EPS:
        return 0.0
    return float(ann_ret / abs(mdd) / 100.0) if ann_ret != 0.0 else 0.0


def compute_max_drawdown(equity_curve: np.ndarray) -> Tuple[float, int, int]:
    """计算最大回撤。

    Returns
    -------
    tuple[float, int, int]
        (max_drawdown_pct, peak_idx, trough_idx)
        回撤为负值，如 -0.25 表示 25% 回撤。
    """
    ec = np.asarray(equity_curve, dtype=np.float64)
    if len(ec) < 2:
        return 0.0, 0, 0

    peak = np.maximum.accumulate(ec)
    drawdown = (ec - peak) / np.maximum(peak, _EPS)
    trough_idx = int(np.nanargmin(drawdown))
    peak_idx = int(np.nanargmax(ec[:trough_idx + 1])) if trough_idx > 0 else 0
    max_dd = float(drawdown[trough_idx])
    return max_dd, peak_idx, trough_idx


def compute_profit_factor(trades: List[dict]) -> float:
    """Profit Factor = 总盈利 / 总亏损绝对值。"""
    if not trades:
        return 0.0
    pnl = np.array([t.get('pnl_pct', 0.0) for t in trades], dtype=np.float64)
    gross_profit = float(np.sum(pnl[pnl > _EPS]))
    gross_loss = float(abs(np.sum(pnl[pnl <= -_EPS])))
    if gross_loss < _EPS:
        return float('inf') if gross_profit > _EPS else 0.0
    return gross_profit / max(gross_loss, _EPS)


def compute_expectancy(trades: List[dict]) -> float:
    """期望值 = 所有交易的平均盈亏（单位为百分比）。"""
    if not trades:
        return 0.0
    pnl = np.array([t.get('pnl_pct', 0.0) for t in trades], dtype=np.float64)
    valid = np.isfinite(pnl)
    if not valid.any():
        return 0.0
    return float(np.mean(pnl[valid])) * 100.0


def compute_annual_return(equity_curve: np.ndarray,
                          periods_per_year: int = 252) -> float:
    """从权益曲线计算年化收益率。"""
    ec = np.asarray(equity_curve, dtype=np.float64)
    if len(ec) < 2:
        return 0.0
    total_ret = ec[-1] / max(ec[0], _EPS)
    n_periods = len(ec) - 1
    if n_periods < 1:
        return 0.0
    ann_factor = periods_per_year / max(n_periods, 1)
    return float((total_ret ** ann_factor - 1.0) * 100.0)


def compute_win_rate(trades: List[dict]) -> float:
    """胜率 = 盈利交易数 / 总交易数 * 100。"""
    if not trades:
        return 0.0
    pnl = np.array([t.get('pnl_pct', 0.0) for t in trades], dtype=np.float64)
    wins = int((pnl > _EPS).sum())
    return float(wins / len(trades)) * 100.0
