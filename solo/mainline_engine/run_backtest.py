"""
机构主线识别系统 — 回测脚本
================================
在历史信号日运行完整流水线，用后续N个交易日的价格模拟交易，
评估系统的选股能力。

用法:
  python -m mainline_engine.run_backtest --signal-date 20260401 --hold-days 20
  python -m mainline_engine.run_backtest --signal-date 20260501 --hold-days 30 --top-n 10
"""
import os
import sys
import argparse
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List

import numpy as np
import pandas as pd
from loguru import logger

from mainline_engine.main import MainlineEngine
from mainline_engine.backtest.engine import BacktestEngine
from mainline_engine.backtest.metrics import compute_metrics, BacktestMetrics
from mainline_engine.data.source import create_from_config


def run_single_backtest(signal_date: str, hold_days: int = 20,
                        top_n: int = 10, config_path: str = None) -> Dict:
    """单次回测：在 signal_date 运行流水线，后续 hold_days 天模拟交易

    Returns:
        {
            'signal_date': str,
            'hold_days': int,
            'metrics': BacktestMetrics,
            'trades': list[dict],
            'signals': list[dict],
        }
    """
    logger.info("=" * 60)
    logger.info(f"回测: 信号日={signal_date}, 持有{hold_days}天, Top{top_n}")
    logger.info("=" * 60)

    # ── 1. 运行流水线获取信号 ──
    engine = MainlineEngine(config_path)
    start_date = (datetime.strptime(signal_date, "%Y%m%d") - timedelta(days=365)).strftime("%Y%m%d")
    results = engine.run_pipeline(start_date=start_date, end_date=signal_date)

    if not results:
        logger.warning("无信号生成，无法回测")
        return {'signal_date': signal_date, 'metrics': BacktestMetrics(), 'trades': [], 'signals': []}

    top_picks = results[:top_n]
    logger.info(f"信号日 {signal_date} 生成 {len(results)} 只候选, 取 Top {top_n}")

    # ── 2. 构建信号DataFrame ──
    signals_list = []
    for r in top_picks:
        if r.buy_signal:
            signals_list.append({
                'trade_date': signal_date,
                'ts_code': r.ts_code,
                'signal_type': r.buy_signal,
                'composite_score': r.composite_score,
            })

    if not signals_list:
        logger.warning("无买入信号，无法回测")
        return {'signal_date': signal_date, 'metrics': BacktestMetrics(), 'trades': [], 'signals': []}

    signals_df = pd.DataFrame(signals_list)
    signal_codes = signals_df['ts_code'].unique().tolist()
    logger.info(f"买入信号: {len(signal_codes)} 只")

    # ── 3. 加载信号日后的价格数据 ──
    # 信号日后 hold_days 个交易日（约 hold_days * 1.5 自然日）
    fwd_end = (datetime.strptime(signal_date, "%Y%m%d") + timedelta(days=int(hold_days * 1.6))).strftime("%Y%m%d")
    ds = engine.data_source
    price_rows = []
    for code in signal_codes:
        df = ds.get_stock_daily(code, signal_date, fwd_end)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                price_rows.append({
                    'trade_date': row.get('trade_date', ''),
                    'ts_code': code,
                    'open': float(row.get('open', 0)),
                    'high': float(row.get('high', 0)),
                    'low': float(row.get('low', 0)),
                    'close': float(row.get('close', 0)),
                })

    prices_df = pd.DataFrame(price_rows)
    if prices_df.empty:
        logger.warning("无前向价格数据，无法回测")
        return {'signal_date': signal_date, 'metrics': BacktestMetrics(), 'trades': [], 'signals': signals_list}

    prices_df['trade_date'] = pd.to_datetime(prices_df['trade_date'], format='%Y%m%d')
    signals_df['trade_date'] = pd.to_datetime(signals_df['trade_date'], format='%Y%m%d')
    logger.info(f"前向价格数据: {len(prices_df)} 条, 覆盖 {prices_df['trade_date'].nunique()} 个交易日")

    # ── 4. 计算ATR用于止损 ──
    # 用信号日前的数据计算ATR
    atr_values = {}
    for code in signal_codes:
        sdf = engine._stock_data.get(code)
        if sdf is not None and len(sdf) >= 20:
            high = sdf['high'].values[-20:].astype(float)
            low = sdf['low'].values[-20:].astype(float)
            close = sdf['close'].values[-20:].astype(float)
            tr = np.maximum(high - low, np.maximum(abs(high - np.roll(close, 1)), abs(low - np.roll(close, 1))))
            tr[0] = high[0] - low[0]
            atr_values[code] = float(np.mean(tr))
        else:
            atr_values[code] = 0.02  # 默认2%

    signals_df['atr_stop'] = signals_df['ts_code'].map(atr_values).fillna(0.02)

    # ── 5. 执行回测 ──
    bt_config = engine.config.get('backtest', {})
    bt_config['max_positions'] = top_n
    bt_engine = BacktestEngine({'backtest': bt_config})
    result = bt_engine.run_backtest(signals_df, prices_df)

    metrics = result.get('metrics', BacktestMetrics())
    trades = result.get('trades', [])

    # ── 6. 打印结果 ──
    logger.info("")
    logger.info("=" * 60)
    logger.info("回测结果")
    logger.info("=" * 60)
    logger.info(f"  信号日: {signal_date}")
    logger.info(f"  持有天数: {hold_days}")
    logger.info(f"  选股数: {len(signal_codes)}")
    logger.info(f"  总交易数: {metrics.total_trades}")
    logger.info(f"  胜率: {metrics.win_rate:.1f}%")
    logger.info(f"  总收益: {metrics.total_return_pct:.2f}%")
    logger.info(f"  年化收益: {metrics.annual_return:.1f}%")
    logger.info(f"  最大回撤: {metrics.max_drawdown_pct:.2f}%")
    logger.info(f"  Sharpe: {metrics.sharpe_ratio:.2f}")
    logger.info(f"  Sortino: {metrics.sortino_ratio:.2f}")
    logger.info(f"  Calmar: {metrics.calmar_ratio:.2f}")
    logger.info(f"  盈亏比: {metrics.profit_factor:.2f}")
    logger.info(f"  期望收益: {metrics.expectancy:.2f}%")
    logger.info(f"  平均盈利: {metrics.avg_win_pct:.2f}%")
    logger.info(f"  平均亏损: {metrics.avg_loss_pct:.2f}%")
    logger.info(f"  最大盈利: {metrics.max_win_pct:.2f}%")
    logger.info(f"  最大亏损: {metrics.max_loss_pct:.2f}%")
    logger.info(f"  平均持仓: {metrics.avg_hold_days:.0f}天")

    # 逐笔交易明细
    if trades:
        logger.info("")
        logger.info("逐笔交易明细:")
        logger.info(f"{'代码':<12} {'买入日':<12} {'买入价':>8} {'卖出日':<12} {'卖出价':>8} {'收益%':>8} {'退出':>8}")
        logger.info("-" * 80)
        for t in trades:
            entry_d = str(t.get('entry_date', ''))[:10]
            exit_d = str(t.get('exit_date', ''))[:10]
            logger.info(f"{t.get('ts_code',''):<12} {entry_d:<12} {t.get('entry_price',0):>8.2f} "
                        f"{exit_d:<12} {t.get('exit_price',0):>8.2f} "
                        f"{t.get('pnl_pct',0)*100:>8.2f} {t.get('exit_reason',''):>8}")

    return {
        'signal_date': signal_date,
        'hold_days': hold_days,
        'metrics': metrics,
        'trades': trades,
        'signals': signals_list,
    }


def run_multi_backtest(signal_dates: List[str], hold_days: int = 20,
                       top_n: int = 10, config_path: str = None) -> Dict:
    """多次回测：在多个信号日分别运行流水线，汇总结果

    Returns:
        {
            'folds': list[Dict],
            'aggregate_metrics': BacktestMetrics,
        }
    """
    all_trades = []
    all_signals = []
    folds = []

    for sd in signal_dates:
        logger.info("")
        logger.info("#" * 60)
        logger.info(f"# 多次回测: {sd}")
        logger.info("#" * 60)
        try:
            result = run_single_backtest(sd, hold_days, top_n, config_path)
            folds.append(result)
            all_trades.extend(result.get('trades', []))
            all_signals.extend(result.get('signals', []))
        except Exception as e:
            logger.error(f"信号日 {sd} 回测失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())

    # 汇总指标
    if all_trades:
        pnl_pcts = np.array([t.get('pnl_pct', 0.0) for t in all_trades])
        wins = pnl_pcts > 1e-10
        n_wins = int(wins.sum())
        n_total = len(all_trades)
        agg = BacktestMetrics(
            total_trades=n_total,
            win_trades=n_wins,
            loss_trades=n_total - n_wins,
            win_rate=float(n_wins / max(n_total, 1)) * 100.0,
            total_return_pct=float(np.sum(pnl_pcts) * 100.0),
            avg_win_pct=float(np.mean(pnl_pcts[wins]) * 100.0) if n_wins > 0 else 0.0,
            avg_loss_pct=float(np.mean(pnl_pcts[~wins]) * 100.0) if (n_total - n_wins) > 0 else 0.0,
            max_win_pct=float(np.max(pnl_pcts) * 100.0) if n_total > 0 else 0.0,
            max_loss_pct=float(np.min(pnl_pcts) * 100.0) if n_total > 0 else 0.0,
            expectancy=float(np.mean(pnl_pcts) * 100.0) if n_total > 0 else 0.0,
        )
        gross_profit = float(np.sum(pnl_pcts[pnl_pcts > 0]))
        gross_loss = float(abs(np.sum(pnl_pcts[pnl_pcts <= 0])))
        agg.profit_factor = gross_profit / max(gross_loss, 1e-10) if gross_loss > 1e-10 else float('inf')
    else:
        agg = BacktestMetrics()

    logger.info("")
    logger.info("=" * 60)
    logger.info("多次回测汇总")
    logger.info("=" * 60)
    logger.info(f"  信号日数: {len(signal_dates)}")
    logger.info(f"  总交易数: {agg.total_trades}")
    logger.info(f"  胜率: {agg.win_rate:.1f}%")
    logger.info(f"  总收益: {agg.total_return_pct:.2f}%")
    logger.info(f"  盈亏比: {agg.profit_factor:.2f}")
    logger.info(f"  期望收益: {agg.expectancy:.2f}%")
    logger.info(f"  平均盈利: {agg.avg_win_pct:.2f}%")
    logger.info(f"  平均亏损: {agg.avg_loss_pct:.2f}%")

    return {'folds': folds, 'aggregate_metrics': agg}


def parse_args():
    parser = argparse.ArgumentParser(description="机构主线识别系统 - 回测")
    parser.add_argument("--signal-date", default=None,
                        help="信号日 YYYYMMDD（单个回测）")
    parser.add_argument("--signal-dates", nargs='+', default=None,
                        help="多个信号日（多次回测）")
    parser.add_argument("--hold-days", type=int, default=20,
                        help="持有天数（默认20）")
    parser.add_argument("--top-n", type=int, default=10,
                        help="每次取Top N股票（默认10）")
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main():
    import logging
    args = parse_args()

    logger.remove()
    logger.add(sys.stderr, level=getattr(logging, args.log_level),
               format="<green>{time:HH:mm:ss}</green> | <level>{level:5s}</level> | <level>{message}</level>")

    if args.signal_dates:
        result = run_multi_backtest(args.signal_dates, args.hold_days, args.top_n, args.config)
    elif args.signal_date:
        result = run_single_backtest(args.signal_date, args.hold_days, args.top_n, args.config)
    else:
        # 默认回测：最近3个月，每月1日
        today = datetime.now()
        dates = []
        for months_ago in [3, 2, 1]:
            d = today - timedelta(days=months_ago * 30)
            d = d.replace(day=1)
            while d.weekday() >= 5:
                d += timedelta(days=1)
            dates.append(d.strftime("%Y%m%d"))
        logger.info(f"默认回测3个信号日: {dates}")
        result = run_multi_backtest(dates, args.hold_days, args.top_n, args.config)


if __name__ == "__main__":
    main()
