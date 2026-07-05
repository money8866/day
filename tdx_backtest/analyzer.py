# -*- coding: utf-8 -*-
"""
Analyzer — 绩效分析 + 可视化

核心指标:
  - 胜率 (Win Rate)
  - 盈亏比 (Profit Factor)
  - 最大回撤 (Max Drawdown)
  - 夏普比率 (Sharpe Ratio)
  - 年化收益率 (Annual Return)
  - 平均持仓天数

可视化:
  - 资金曲线 (Equity Curve)
  - 回撤曲线 (Drawdown Curve)
  - K线图 + 买卖信号标记
"""
from __future__ import annotations
from typing import Optional

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle

# Windows 中文显示
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

from backtest import BacktestResult, TradeRecord


# =========================================================
# 核心统计指标
# =========================================================
def compute_metrics(result: BacktestResult, risk_free: float = 0.03) -> dict:
    """计算全部绩效指标

    Args:
        result: BacktestResult 对象
        risk_free: 无风险年化利率 (默认 3%)

    Returns:
        dict 指标字典
    """
    eq = result.equity_curve
    trades = result.trades
    daily_ret = result.daily_returns

    # 总收益率
    total_return = (result.final_equity / result.initial_capital - 1) * 100

    # 年化收益率 (252 交易日)
    n_days = len(eq)
    annual_return = ((result.final_equity / result.initial_capital) ** (252 / max(n_days, 1)) - 1) * 100

    # 最大回撤
    cummax = eq.cummax()
    drawdown = (eq - cummax) / cummax * 100
    max_drawdown = abs(drawdown.min())

    # 夏普比率 (日收益 * 252 - rf) / 日波动 * sqrt(252)
    if daily_ret.std() > 0:
        sharpe = (daily_ret.mean() * 252 - risk_free) / (daily_ret.std() * np.sqrt(252))
    else:
        sharpe = 0.0

    # 交易统计
    n_trades = len(trades)
    wins = [t for t in trades if t.profit > 0]
    losses = [t for t in trades if t.profit <= 0]
    win_rate = len(wins) / n_trades * 100 if n_trades > 0 else 0

    # 盈亏比 = 总盈利 / 总亏损
    total_win = sum(t.profit for t in wins)
    total_loss = abs(sum(t.profit for t in losses))
    profit_factor = total_win / total_loss if total_loss > 0 else float("inf")

    # 平均持仓天数
    avg_hold = np.mean([t.hold_days for t in trades]) if trades else 0

    # 单笔最大盈利/亏损
    max_win = max([t.profit for t in trades], default=0)
    max_loss = min([t.profit for t in trades], default=0)

    return {
        "initial_capital":   result.initial_capital,
        "final_equity":      result.final_equity,
        "total_return_pct":  round(total_return, 2),
        "annual_return_pct": round(annual_return, 2),
        "max_drawdown_pct":  round(max_drawdown, 2),
        "sharpe_ratio":      round(sharpe, 3),
        "n_trades":          n_trades,
        "win_rate_pct":      round(win_rate, 1),
        "profit_factor":     round(profit_factor, 2),
        "avg_hold_days":     round(avg_hold, 1),
        "max_win":           round(max_win, 2),
        "max_loss":          round(max_loss, 2),
        "total_win":         round(total_win, 2),
        "total_loss":        round(total_loss, 2),
    }


def print_metrics(metrics: dict) -> None:
    """格式化打印绩效指标"""
    print("\n" + "=" * 50)
    print("  回测绩效报告")
    print("=" * 50)
    print(f"  初始资金:      ¥{metrics['initial_capital']:>12,.0f}")
    print(f"  最终权益:      ¥{metrics['final_equity']:>12,.0f}")
    print(f"  总收益率:      {metrics['total_return_pct']:>12.2f}%")
    print(f"  年化收益率:    {metrics['annual_return_pct']:>12.2f}%")
    print(f"  最大回撤:      {metrics['max_drawdown_pct']:>12.2f}%")
    print(f"  夏普比率:      {metrics['sharpe_ratio']:>12.3f}")
    print("-" * 50)
    print(f"  交易次数:      {metrics['n_trades']:>12d}")
    print(f"  胜率:          {metrics['win_rate_pct']:>12.1f}%")
    print(f"  盈亏比:        {metrics['profit_factor']:>12.2f}")
    print(f"  平均持仓天数:  {metrics['avg_hold_days']:>12.1f}")
    print("-" * 50)
    print(f"  总盈利:        ¥{metrics['total_win']:>12,.2f}")
    print(f"  总亏损:        ¥{metrics['total_loss']:>12,.2f}")
    print(f"  单笔最大盈利:  ¥{metrics['max_win']:>12,.2f}")
    print(f"  单笔最大亏损:  ¥{metrics['max_loss']:>12,.2f}")
    print("=" * 50)


# =========================================================
# 可视化
# =========================================================
def plot_equity_curve(result: BacktestResult, title: str = "资金曲线",
                      save_path: Optional[str] = None) -> None:
    """绘制资金曲线 + 回撤曲线"""
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True,
                              gridspec_kw={"height_ratios": [3, 1]})

    eq = result.equity_curve
    dates = pd.to_datetime(eq.index, format="%Y%m%d")

    # 资金曲线
    ax1 = axes[0]
    ax1.plot(dates, eq.values, color="#1f77b4", linewidth=1.5, label="总资产")
    ax1.fill_between(dates, result.initial_capital, eq.values,
                     where=(eq.values >= result.initial_capital),
                     alpha=0.2, color="green", label="盈利")
    ax1.fill_between(dates, result.initial_capital, eq.values,
                     where=(eq.values < result.initial_capital),
                     alpha=0.2, color="red", label="亏损")
    ax1.axhline(y=result.initial_capital, color="gray", linestyle="--", alpha=0.5)
    ax1.set_title(title, fontsize=14)
    ax1.set_ylabel("资金 (¥)")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)

    # 回撤曲线
    ax2 = axes[1]
    cummax = eq.cummax()
    drawdown = (eq - cummax) / cummax * 100
    ax2.fill_between(dates, 0, drawdown.values, color="red", alpha=0.4)
    ax2.plot(dates, drawdown.values, color="red", linewidth=0.8)
    ax2.set_ylabel("回撤 (%)")
    ax2.set_xlabel("日期")
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"[图表已保存] {save_path}")
    plt.show()


def plot_kline_with_signals(df: pd.DataFrame, title: str = "K线 + 买卖信号",
                            n_bars: int = 120,
                            save_path: Optional[str] = None) -> None:
    """绘制 K 线图 + 买卖箭头标记

    Args:
        df: 含 buy_signal/sell_signal 列的 K线数据
        n_bars: 只显示最后 N 根 K 线
    """
    df_plot = df.tail(n_bars).copy().reset_index(drop=True)
    dates = pd.to_datetime(df_plot["trade_date"], format="%Y%m%d")

    fig, ax = plt.subplots(figsize=(14, 7))

    # 绘制 K 线 (简化版: 用收盘价折线 + 影线)
    for i, row in df_plot.iterrows():
        color = "red" if row["close"] >= row["open"] else "green"
        # 影线
        ax.plot([i, i], [row["low"], row["high"]], color=color, linewidth=0.8)
        # 实体
        body_low = min(row["open"], row["close"])
        body_high = max(row["open"], row["close"])
        ax.add_patch(Rectangle((i - 0.3, body_low), 0.6, max(body_high - body_low, 0.001),
                                facecolor=color, edgecolor=color, alpha=0.8))

    # 买入信号: 向上箭头 (绿色三角形)
    buy_idx = df_plot.index[df_plot["buy_signal"] == True].tolist()
    if buy_idx:
        ax.scatter(buy_idx, df_plot.loc[buy_idx, "low"] * 0.995,
                   marker="^", color="red", s=120, zorder=5, label="买入")

    # 卖出信号: 向下箭头 (红色倒三角)
    sell_idx = df_plot.index[df_plot["sell_signal"] == True].tolist()
    if sell_idx:
        ax.scatter(sell_idx, df_plot.loc[sell_idx, "high"] * 1.005,
                   marker="v", color="green", s=120, zorder=5, label="卖出")

    # X 轴日期
    step = max(len(df_plot) // 10, 1)
    ax.set_xticks(range(0, len(df_plot), step))
    ax.set_xticklabels([d.strftime("%Y-%m-%d") for d in dates[::step]], rotation=30)

    ax.set_title(title, fontsize=14)
    ax.set_ylabel("价格")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"[图表已保存] {save_path}")
    plt.show()


def plot_trade_distribution(trades: list, save_path: Optional[str] = None) -> None:
    """绘制交易盈亏分布直方图"""
    if not trades:
        print("无交易记录")
        return

    profits = [t.profit for t in trades]
    fig, ax = plt.subplots(figsize=(10, 5))
    colors = ["green" if p > 0 else "red" for p in profits]
    ax.bar(range(len(profits)), profits, color=colors, alpha=0.7)
    ax.axhline(y=0, color="black", linewidth=0.8)
    ax.set_title("每笔交易盈亏分布", fontsize=14)
    ax.set_xlabel("交易序号")
    ax.set_ylabel("盈亏 (¥)")
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"[图表已保存] {save_path}")
    plt.show()


# =========================================================
# 自检
# =========================================================
if __name__ == "__main__":
    from data_loader import load_kline
    from strategy import DoubleMAStrategy
    from backtest import Backtester

    # 运行完整回测
    df = load_kline("000001.SZ", start_date="20230101")
    if df.empty:
        print("无数据")
        raise SystemExit

    strat = DoubleMAStrategy(5, 10)
    df_sig = strat.generate_signals(df)

    bt = Backtester(initial_capital=100_000)
    result = bt.run_single(df_sig)

    metrics = compute_metrics(result)
    print_metrics(metrics)

    # 可视化 (注释掉以避免阻塞, 取消注释查看图表)
    # plot_equity_curve(result, title=f"{strat.name} - 平安银行")
    # plot_kline_with_signals(df_sig, title=f"{strat.name} - 买卖信号", n_bars=120)
    # plot_trade_distribution(result.trades)
