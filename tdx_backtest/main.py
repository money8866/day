# -*- coding: utf-8 -*-
"""
TDX Backtest — 主程序入口

演示:
  1. 双均线交叉策略 (MA5/MA10) 单标的回测
  2. MACD + RSI 策略 单标的回测
  3. 配置驱动策略 (DSL) 单标的回测
  4. 多标的轮动回测

用法:
  python main.py                    # 默认运行双均线策略
  python main.py --code 600519.SH   # 指定股票
  python main.py --multi            # 多标的轮动
  python main.py --plot             # 显示图表
"""
from __future__ import annotations
import os
import sys
import argparse
from datetime import datetime

# 把当前目录加入 sys.path, 方便 import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_loader import load_kline, load_multiple, list_all_codes
from strategy import (
    DoubleMAStrategy, MACD_RSI_Strategy, BollBreakStrategy, ConfigStrategy
)
from backtest import Backtester, CostConfig
from analyzer import compute_metrics, print_metrics, plot_equity_curve, plot_kline_with_signals, plot_trade_distribution


# =========================================================
# 单标的回测流程
# =========================================================
def run_single_backtest(ts_code: str,
                        strategy,
                        start_date: str = "20230101",
                        end_date: str = None,
                        initial_capital: float = 100_000,
                        plot: bool = False,
                        save_dir: str = None) -> None:
    """运行单标的回测并打印结果

    Args:
        ts_code: 股票代码
        strategy: 策略对象 (StrategyBase 子类)
        start_date, end_date: 回测区间
        initial_capital: 初始资金
        plot: 是否显示图表
        save_dir: 图表保存目录 (None 不保存)
    """
    print(f"\n{'=' * 70}")
    print(f"  回测: {ts_code} | 策略: {strategy.name} | 区间: {start_date} ~ {end_date or '最新'}")
    print(f"{'=' * 70}")

    # 1. 加载数据
    df = load_kline(ts_code, start_date=start_date, end_date=end_date)
    if df.empty:
        print(f"  [ERROR] 无数据: {ts_code}")
        return
    print(f"  数据: {len(df)} 条, {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}")

    # 2. 生成信号
    df_sig = strategy.generate_signals(df)
    n_buy = df_sig["buy_signal"].sum()
    n_sell = df_sig["sell_signal"].sum()
    print(f"  信号: 买入 {n_buy} 次, 卖出 {n_sell} 次")

    # 3. 回测
    bt = Backtester(initial_capital=initial_capital)
    result = bt.run_single(df_sig)

    # 4. 绩效分析
    metrics = compute_metrics(result)
    print_metrics(metrics)

    # 5. 交易明细 (前10笔)
    if result.trades:
        print(f"\n  前 10 笔交易:")
        print(f"  {'买入日':<10} {'买价':>8} {'卖出日':<10} {'卖价':>8} {'天数':>4} {'盈亏':>10} {'收益率':>8}")
        print(f"  {'-' * 66}")
        for t in result.trades[:10]:
            print(f"  {t.buy_date:<10} {t.buy_price:>8.2f} {t.sell_date:<10} {t.sell_price:>8.2f} "
                  f"{t.hold_days:>4d} {t.profit:>+10.2f} {t.pct_return:>+7.2f}%")

    # 6. 可视化
    if plot:
        save_path = None
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
            save_path = os.path.join(save_dir, f"equity_{ts_code.replace('.', '_')}_{strategy.name}.png")
        plot_equity_curve(result, title=f"{strategy.name} - {ts_code}", save_path=save_path)

        if save_dir:
            kline_path = os.path.join(save_dir, f"kline_{ts_code.replace('.', '_')}_{strategy.name}.png")
        else:
            kline_path = None
        plot_kline_with_signals(df_sig, title=f"{strategy.name} - {ts_code} 买卖信号",
                                n_bars=120, save_path=kline_path)

        if save_dir and result.trades:
            trade_path = os.path.join(save_dir, f"trades_{ts_code.replace('.', '_')}_{strategy.name}.png")
            plot_trade_distribution(result.trades, save_path=trade_path)


# =========================================================
# 多标的轮动回测
# =========================================================
def run_rotation_backtest(codes: list,
                          strategy,
                          start_date: str = "20230101",
                          end_date: str = None,
                          initial_capital: float = 100_000,
                          plot: bool = False,
                          save_dir: str = None) -> None:
    """多标的轮动回测

    Args:
        codes: 候选股票池
        strategy: 策略对象
    """
    print(f"\n{'=' * 70}")
    print(f"  多标的轮动回测 | 策略: {strategy.name}")
    print(f"  候选池: {len(codes)} 只 | 区间: {start_date} ~ {end_date or '最新'}")
    print(f"{'=' * 70}")

    # 1. 加载全部候选股数据
    print(f"  加载数据中...")
    df_pool = load_multiple(codes, start_date=start_date, end_date=end_date)
    if df_pool.empty:
        print(f"  [ERROR] 无数据")
        return
    print(f"  数据: {len(df_pool)} 条, {df_pool['ts_code'].nunique()} 只股票")

    # 2. 逐股生成信号
    print(f"  生成信号中...")
    frames = []
    for code in df_pool["ts_code"].unique():
        df_code = df_pool[df_pool["ts_code"] == code].copy()
        df_sig = strategy.generate_signals(df_code)
        frames.append(df_sig)
    df_pool_sig = __import__("pandas").concat(frames, ignore_index=True)

    n_buy = df_pool_sig["buy_signal"].sum()
    print(f"  全部买入信号: {n_buy} 次")

    # 3. 回测
    bt = Backtester(initial_capital=initial_capital)
    result = bt.run_rotation(df_pool_sig)

    # 4. 绩效
    metrics = compute_metrics(result)
    print_metrics(metrics)

    # 5. 可视化
    if plot:
        save_path = None
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
            save_path = os.path.join(save_dir, f"rotation_equity_{strategy.name}.png")
        plot_equity_curve(result, title=f"轮动 - {strategy.name}", save_path=save_path)


# =========================================================
# 主入口
# =========================================================
def main():
    parser = argparse.ArgumentParser(description="TDX 通达信本地回测系统")
    parser.add_argument("--code", type=str, default="000001.SZ",
                        help="股票代码 (默认 000001.SZ 平安银行)")
    parser.add_argument("--start", type=str, default="20230101",
                        help="起始日期 YYYYMMDD")
    parser.add_argument("--end", type=str, default=None,
                        help="结束日期 YYYYMMDD")
    parser.add_argument("--capital", type=float, default=100_000,
                        help="初始资金 (默认 10万)")
    parser.add_argument("--strategy", type=str, default="dma",
                        choices=["dma", "macd_rsi", "boll", "config"],
                        help="策略: dma=双均线 / macd_rsi / boll / config")
    parser.add_argument("--multi", action="store_true",
                        help="多标的轮动模式")
    parser.add_argument("--pool", type=str, default="000001.SZ,600519.SH,000858.SZ",
                        help="轮动候选池 (逗号分隔)")
    parser.add_argument("--plot", action="store_true",
                        help="显示图表")
    parser.add_argument("--save", type=str, default=None,
                        help="图表保存目录")
    args = parser.parse_args()

    # 选择策略
    if args.strategy == "dma":
        strategy = DoubleMAStrategy(fast=5, slow=10)
    elif args.strategy == "macd_rsi":
        strategy = MACD_RSI_Strategy(rsi_max=70)
    elif args.strategy == "boll":
        strategy = BollBreakStrategy(period=20, nbdev=2)
    elif args.strategy == "config":
        cfg = {
            "buy":  ["CROSS(MA5, MA10)", "RSI6 < 70"],
            "sell": ["CROSS_DOWN(MA5, MA10)"],
        }
        strategy = ConfigStrategy(cfg)
    else:
        strategy = DoubleMAStrategy(5, 10)

    save_dir = args.save or os.path.join(os.path.dirname(__file__), "output")

    if args.multi:
        codes = [c.strip() for c in args.pool.split(",")]
        run_rotation_backtest(codes, strategy, args.start, args.end,
                              args.capital, args.plot, save_dir)
    else:
        run_single_backtest(args.code, strategy, args.start, args.end,
                            args.capital, args.plot, save_dir)


if __name__ == "__main__":
    main()
