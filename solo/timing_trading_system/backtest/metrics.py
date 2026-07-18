#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
绩效指标计算模块
================
根据回测结果（净值曲线 + 交易列表）计算各类绩效指标，
并提供格式化的报告输出和可视化功能。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .engine import BacktestTrade

LOG = logging.getLogger("timing_trading.backtest.metrics")

# ─────────────────────────────────────────────────────────────────
# 核心指标计算
# ─────────────────────────────────────────────────────────────────


def calc_metrics(
    equity_curve: List[dict],
    trades: List[BacktestTrade],
    initial_capital: float,
    risk_free_rate: float = 0.02,
) -> dict:
    """计算绩效指标

    参数:
        equity_curve: 净值曲线列表（含 trade_date, total_equity, daily_return 等字段）
        trades: 已平仓交易列表
        initial_capital: 初始资金
        risk_free_rate: 无风险利率（默认 2%）

    返回:
        {
            "TotalReturn": ...,
            "AnnualReturn": ...,
            "AnnualVol": ...,
            "Sharpe": ...,
            "MaxDrawdown": ...,
            "Calmar": ...,
            "WinRate": ...,
            "ProfitLossRatio": ...,
            "TotalTrades": ...,
            "AvgHoldDays": ...,
            "MaxProfit": ...,
            "MaxLoss": ...,
            "MonthlyWinRate": ...,
            "details": {...}
        }
    """
    if not equity_curve:
        return {"error": "净值曲线为空"}

    # ── 转换为 DataFrame 方便计算 ──
    eq_df = pd.DataFrame(equity_curve)
    eq_df["trade_date"] = pd.to_datetime(eq_df["trade_date"], format="%Y%m%d")
    eq_df = eq_df.sort_values("trade_date").reset_index(drop=True)

    first_equity = float(eq_df["total_equity"].iloc[0])
    last_equity = float(eq_df["total_equity"].iloc[-1])
    first_date = eq_df["trade_date"].iloc[0]
    last_date = eq_df["trade_date"].iloc[-1]

    # 回测总天数（自然日）
    total_days = (last_date - first_date).days
    total_years = max(total_days / 365.0, 1 / 365.0)

    # ── 总收益率 ──
    total_return = (last_equity / initial_capital - 1) * 100

    # ── 年化收益率 ──
    if first_equity > 0:
        annual_return = ((last_equity / first_equity) ** (1.0 / total_years) - 1) * 100
    else:
        annual_return = 0.0

    # ── 年化波动率 ──
    # 使用每日收益率计算
    daily_returns = eq_df["daily_return"].values / 100.0  # 转为小数
    if len(daily_returns) > 1:
        # 年化波动率 = 日收益率标准差 * sqrt(252)
        daily_std = np.std(daily_returns, ddof=1)
        annual_vol = daily_std * np.sqrt(252) * 100
    else:
        annual_vol = 0.0

    # ── 夏普比率 ──
    if annual_vol > 1e-10:
        sharpe = (annual_return - risk_free_rate * 100) / annual_vol
    else:
        sharpe = 0.0

    # ── 最大回撤 ──
    # 从 equity_curve 中提取已有 drawdown 字段
    drawdowns = eq_df["drawdown"].values
    max_drawdown = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0.0

    # ── 卡玛比 ──
    calmar = annual_return / abs(max_drawdown) if abs(max_drawdown) > 1e-10 else 0.0

    # ── 从交易列表计算的指标 ──
    if trades:
        total_trades = len(trades)
        win_trades = [t for t in trades if t.pnl_pct > 0]
        loss_trades = [t for t in trades if t.pnl_pct <= 0]
        win_count = len(win_trades)
        loss_count = len(loss_trades)

        # 胜率
        win_rate = (win_count / total_trades) * 100 if total_trades > 0 else 0.0

        # 盈亏比（平均盈利 / 平均亏损的绝对值）
        avg_profit = np.mean([t.pnl_pct for t in win_trades]) if win_trades else 0.0
        avg_loss = np.mean([t.pnl_pct for t in loss_trades]) if loss_trades else 0.0
        profit_loss_ratio = abs(avg_profit / avg_loss) if avg_loss != 0 else float("inf")

        # 平均持仓天数
        avg_hold_days = np.mean([t.hold_days for t in trades])

        # 单笔最大盈利 / 最大亏损
        max_profit = max(t.pnl_pct for t in trades)
        max_loss = min(t.pnl_pct for t in trades)

        # 盈亏总和
        total_profit = sum(t.pnl_amount for t in win_trades)
        total_loss = abs(sum(t.pnl_amount for t in loss_trades))
        profit_factor = total_profit / total_loss if total_loss > 0 else float("inf")

    else:
        total_trades = 0
        win_rate = 0.0
        profit_loss_ratio = 0.0
        avg_hold_days = 0.0
        max_profit = 0.0
        max_loss = 0.0
        profit_factor = 0.0

    # ── 月度胜率 ──
    monthly_stats = calc_monthly_returns(eq_df)
    monthly_win_rate = 0.0
    monthly_total = 0
    monthly_win = 0
    if isinstance(monthly_stats, pd.DataFrame) and not monthly_stats.empty:
        monthly_total = int(monthly_stats.count().sum())
        monthly_win = int((monthly_stats > 0).sum().sum())
        monthly_win_rate = (monthly_win / monthly_total * 100) if monthly_total > 0 else 0.0

    # 构造结果
    metrics = {
        "TotalReturn": round(total_return, 2),
        "AnnualReturn": round(annual_return, 2),
        "AnnualVol": round(annual_vol, 2),
        "Sharpe": round(sharpe, 4),
        "MaxDrawdown": round(max_drawdown, 2),
        "Calmar": round(calmar, 4),
        "WinRate": round(win_rate, 2),
        "ProfitLossRatio": round(profit_loss_ratio, 4),
        "TotalTrades": total_trades,
        "AvgHoldDays": round(avg_hold_days, 1),
        "MaxProfit": round(max_profit, 2),
        "MaxLoss": round(max_loss, 2),
        "ProfitFactor": round(profit_factor, 4),
        "MonthlyWinRate": round(monthly_win_rate, 2),
        "MonthlyWinMonths": int(monthly_win),
        "MonthlyTotalMonths": int(monthly_total),
        "details": {
            "initial_capital": round(initial_capital, 2),
            "final_capital": round(last_equity, 2),
            "total_days": int(total_days),
            "total_years": round(total_years, 2),
            "win_trades": win_count if trades else 0,
            "loss_trades": loss_count if trades else 0,
            "avg_profit_pct": round(float(avg_profit), 2) if trades and win_trades else 0.0,
            "avg_loss_pct": round(float(avg_loss), 2) if trades and loss_trades else 0.0,
        },
    }

    return metrics


# ─────────────────────────────────────────────────────────────────
# 月度收益率矩阵
# ─────────────────────────────────────────────────────────────────


def calc_monthly_returns(equity_curve: pd.DataFrame) -> pd.DataFrame:
    """计算月度收益率矩阵

    参数:
        equity_curve: DataFrame，必须包含 trade_date 和 daily_return 列

    返回:
        DataFrame: 行=年份, 列=月份, 值为该月收益率(%)
    """
    if equity_curve.empty:
        return pd.DataFrame()

    df = equity_curve.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)

    # 按月份分组，计算月收益率
    df["year"] = df["trade_date"].dt.year
    df["month"] = df["trade_date"].dt.month

    # 用每月的累计 daily_return 作为月收益率
    monthly_returns = df.groupby(["year", "month"])["daily_return"].sum().reset_index()
    monthly_returns.rename(columns={"daily_return": "return"}, inplace=True)
    monthly_returns["return"] = monthly_returns["return"].round(2)

    # 转换为矩阵形式
    pivot = monthly_returns.pivot_table(
        index="year",
        columns="month",
        values="return",
        aggfunc="sum",
    )
    # 确保月份列完整
    for m in range(1, 13):
        if m not in pivot.columns:
            pivot[m] = 0.0
    pivot = pivot[sorted(pivot.columns)]
    pivot.columns = [f"{m}月" for m in pivot.columns]

    return pivot


# ─────────────────────────────────────────────────────────────────
# 格式化输出
# ─────────────────────────────────────────────────────────────────


def format_metrics_report(metrics: dict) -> str:
    """格式化输出回测报告（markdown格式）"""
    if not metrics or "error" in metrics:
        return f"❌ 指标计算错误: {metrics.get('error', '未知')}"

    lines = []
    lines.append("## 📊 回测绩效报告\n")
    lines.append("---\n")

    # 核心指标
    lines.append("### 核心指标\n")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")

    core_items = [
        ("总收益率", f"{metrics.get('TotalReturn', 'N/A')}%"),
        ("年化收益率", f"{metrics.get('AnnualReturn', 'N/A')}%"),
        ("年化波动率", f"{metrics.get('AnnualVol', 'N/A')}%"),
        ("夏普比率", f"{metrics.get('Sharpe', 'N/A')}"),
        ("最大回撤", f"{metrics.get('MaxDrawdown', 'N/A')}%"),
        ("卡玛比", f"{metrics.get('Calmar', 'N/A')}"),
    ]
    for name, val in core_items:
        lines.append(f"| {name} | {val} |")

    lines.append("")
    lines.append("### 交易统计\n")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")

    trade_items = [
        ("总交易次数", f"{metrics.get('TotalTrades', 'N/A')}"),
        ("胜率", f"{metrics.get('WinRate', 'N/A')}%"),
        ("盈亏比", f"{metrics.get('ProfitLossRatio', 'N/A')}"),
        ("平均持仓天数", f"{metrics.get('AvgHoldDays', 'N/A')}"),
        ("单笔最大盈利", f"{metrics.get('MaxProfit', 'N/A')}%"),
        ("单笔最大亏损", f"{metrics.get('MaxLoss', 'N/A')}%"),
        ("盈亏因子", f"{metrics.get('ProfitFactor', 'N/A')}"),
    ]
    for name, val in trade_items:
        lines.append(f"| {name} | {val} |")

    lines.append("")
    lines.append("### 月度统计\n")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 月度胜率 | {metrics.get('MonthlyWinRate', 'N/A')}% |")
    lines.append(f"| 盈利月数/总月数 | {metrics.get('MonthlyWinMonths', 0)}/{metrics.get('MonthlyTotalMonths', 0)} |")

    # 详情
    details = metrics.get("details", {})
    if details:
        lines.append("")
        lines.append("### 详情\n")
        lines.append("| 项目 | 数值 |")
        lines.append("|------|------|")
        detail_items = [
            ("初始资金", f"{details.get('initial_capital', 'N/A')}"),
            ("最终资金", f"{details.get('final_capital', 'N/A')}"),
            ("回测天数", f"{details.get('total_days', 'N/A')}"),
            ("盈利交易数", f"{details.get('win_trades', 'N/A')}"),
            ("亏损交易数", f"{details.get('loss_trades', 'N/A')}"),
            ("平均盈利", f"{details.get('avg_profit_pct', 'N/A')}%"),
            ("平均亏损", f"{details.get('avg_loss_pct', 'N/A')}%"),
        ]
        for name, val in detail_items:
            lines.append(f"| {name} | {val} |")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────
# 净值曲线可视化
# ─────────────────────────────────────────────────────────────────


def plot_equity_curve(equity_curve: List[dict], save_path: str = ""):
    """绘制净值曲线图

    参数:
        equity_curve: 净值曲线列表，每项含 trade_date, total_equity,
                      position_value, cash, drawdown 等字段
        save_path: 保存路径，为空则直接显示
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mticker
        from matplotlib.dates import DateFormatter
    except ImportError:
        LOG.warning("matplotlib 未安装，无法绘制净值曲线")
        return

    if not equity_curve:
        LOG.warning("净值曲线为空，无法绘制")
        return

    df = pd.DataFrame(equity_curve)
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df = df.sort_values("trade_date").reset_index(drop=True)

    # 创建双轴图
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10), gridspec_kw={"height_ratios": [3, 1]})
    fig.suptitle("回测净值曲线", fontsize=14, fontweight="bold")

    # ── 上方：净值曲线 + 持仓市值 ──
    ax1.plot(df["trade_date"], df["total_equity"], label="总资产", color="#1f77b4", linewidth=1.8)
    ax1.fill_between(df["trade_date"], df["total_equity"], df["total_equity"].iloc[0],
                     alpha=0.08, color="#1f77b4")
    ax1.plot(df["trade_date"], df["cash"], label="现金", color="#2ca02c", linewidth=1.0, alpha=0.7)
    ax1.plot(df["trade_date"], df["position_value"], label="持仓市值", color="#ff7f0e", linewidth=1.0, alpha=0.7)

    # 标注初始资金线
    initial = df["total_equity"].iloc[0]
    ax1.axhline(y=initial, color="gray", linestyle="--", alpha=0.4, label=f"初始资金({initial:.0f})")

    ax1.set_ylabel("资金 (元)")
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(True, alpha=0.3)
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))

    # ── 下方：回撤曲线 ──
    if "drawdown" in df.columns:
        ax2.fill_between(df["trade_date"], 0, df["drawdown"],
                         alpha=0.4, color="red", label="回撤")
        ax2.plot(df["trade_date"], df["drawdown"], color="darkred", linewidth=1.0)
        ax2.set_ylabel("回撤 (%)")
        ax2.set_xlabel("日期")
        ax2.legend(loc="lower left", fontsize=9)
        ax2.grid(True, alpha=0.3)

    # 日期格式化
    ax2.xaxis.set_major_formatter(DateFormatter("%Y-%m"))
    plt.xticks(rotation=45)
    plt.tight_layout()

    # ── 保存或显示 ──
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        LOG.info("净值曲线已保存至: %s", save_path)
    else:
        plt.show()
