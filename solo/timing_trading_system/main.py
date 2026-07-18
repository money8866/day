#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
三层择时交易系统 - 主入口
=========================
支持四种运行模式：
  1. daily  - 每日运行：加载股池 → 大盘评估 → 信号生成 → 仓位建议
  2. backtest - 回测模式：Walk-Forward回测验证
  3. train  - 训练模式：训练LightGBM动态权重模型
  4. optimize - 优化模式：网格搜索+Walk-Forward参数优化
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional

# 添加项目根目录到sys.path
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import yaml
import pandas as pd

LOG = logging.getLogger("timing_trading")


# ─────────────────────────────────────────────────────────────────
# 配置加载
# ─────────────────────────────────────────────────────────────────

def load_config(config_path: str = "") -> dict:
    """加载YAML配置文件"""
    if not config_path:
        config_path = os.path.join(_PROJECT_ROOT, "config.yaml")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    LOG.info("配置加载完成: %s", config_path)
    return config


# ─────────────────────────────────────────────────────────────────
# 日志设置
# ─────────────────────────────────────────────────────────────────

def setup_logging(verbose: bool = False):
    """配置日志"""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )
    # 抑制某些模块的冗长日志
    logging.getLogger("matplotlib").setLevel(logging.WARNING)


# ─────────────────────────────────────────────────────────────────
# 模式1: 每日运行
# ─────────────────────────────────────────────────────────────────

def run_daily(config: dict, trade_date: str = ""):
    """每日运行模式"""
    from data import pool_loader
    from market.market_timing import MarketTimingEngine
    from theme.theme_timing import ThemeTimingEngine, match_pool_to_themes
    from trading.signal import SignalFusionEngine
    from trading.position import calculate_positions, get_position_summary

    LOG.info("=" * 60)
    LOG.info("三层择时系统 - 每日运行")
    LOG.info("=" * 60)

    # 1. 加载基本面股池
    pool_path = config.get("general", {}).get("stock_pool_path", "")
    pool_df = pool_loader.load_pool(pool_path)
    LOG.info("股池: %d 只股票", len(pool_df))
    print(pool_loader.get_pool_stats(pool_df))

    # 2. 加载主题映射
    theme_map_path = config.get("general", {}).get("theme_map_path", "")
    theme_map = pool_loader.load_theme_map(theme_map_path)

    # 3. 大盘择时
    market_engine = MarketTimingEngine(config)
    market_state = market_engine.evaluate(trade_date=trade_date)
    LOG.info("大盘状态: %s | 评分: %.0f | 建议仓位: %.0f%%",
             market_state.label, market_state.score,
             market_state.position_suggest * 100)
    print(f"\n[大盘状态] {market_state.label}")
    print(f"  评分: {market_state.score:.0f}/100 | 仓位建议: {market_state.position_suggest*100:.0f}%")
    print(f"  ADX: {market_state.adx:.1f} | 涨跌比: {market_state.advance_ratio:.2f}")
    print(f"  MA排列: {market_state.ma_arrangement} | MA20方向: {market_state.details.get('ma20_trend', 'N/A')}")

    # 4. 主题择时
    if config.get("theme_timing", {}).get("enabled", True):
        theme_engine = ThemeTimingEngine(config)
        theme_engine.load_theme_map(theme_map_path)
        # 需要个股日线数据来计算趋势分数（传空字典跳过）
        theme_states = theme_engine.evaluate(pool_df, trade_date=trade_date)
        pool_df = match_pool_to_themes(pool_df, theme_states)

        print(f"\n[主题择时] Top {len(theme_states)}")
        for ts in theme_states[:5]:
            print(f"  {ts.name}: score={ts.score:.0f} | trend={ts.trend_score:.0f} breadth={ts.breadth_score:.0f} leader={ts.leader_health_score:.0f}")
    else:
        theme_states = []

    # 5. 信号融合
    fusion_engine = SignalFusionEngine(config)
    signals = fusion_engine.evaluate(pool_df, trade_date=trade_date, force_retrain=False)

    # 6. 仓位计算
    position_df = calculate_positions(signals, config,
                                       market_state.position_suggest)

    # 7. 输出结果
    print(f"\n{'='*60}")
    print(f"[择时信号结果] 日期: {trade_date or '最新'}")
    print(f"{'='*60}")

    if not position_df.empty:
        summary = get_position_summary(position_df)
        print(f"\n[仓位建议] 总仓位: {summary['total_capital_ratio']}% | {summary['total_positions']} 只标的")
        print(f"  平均评分: {summary['avg_score']} | 最大单只: {summary['top_position']}%")
        print(f"\n[持仓明细]")
        for _, row in position_df.iterrows():
            print(f"  {row['stock_name']}({row['ts_code']}): "
                  f"{row['position_pct']}% | score={row['composite_score']:.0f} "
                  f"| entry={row['primary_entry']} | theme={row.get('theme','')}")

        # 保存到CSV
        output_dir = config.get("general", {}).get("output_dir", "output")
        os.makedirs(os.path.join(_PROJECT_ROOT, output_dir, "reports"), exist_ok=True)
        date_str = trade_date or datetime.now().strftime("%Y%m%d")
        csv_path = os.path.join(_PROJECT_ROOT, output_dir, "reports",
                                f"timing_signals_{date_str}.csv")
        position_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        LOG.info("结果已保存: %s", csv_path)
    else:
        print("\n[无买入信号] 建议空仓或极轻仓等待")
        print(f"  基础仓位: {config.get('position', {}).get('base_position', 0.2)*100:.0f}%")

    # 大盘建议摘要
    print(f"\n[操作建议]")
    if market_state.name == "bull_market":
        print(f"  → 主升浪行情，积极操作，聚焦强势股")
    elif market_state.name == "bear_market":
        print(f"  → 主跌行情，严格控制仓位，多看少动")
    elif market_state.name == "strong_oscillate":
        print(f"  → 震荡偏强，高抛低吸，注意节奏")
    else:
        print(f"  → 中期调整，低吸潜伏，等待信号")

    return position_df


# ─────────────────────────────────────────────────────────────────
# 模式2: 回测
# ─────────────────────────────────────────────────────────────────

def run_backtest(config: dict, start_date: str, end_date: str = "",
                 output_report: bool = True):
    """运行回测"""
    LOG.info("=" * 60)
    LOG.info("三层择时系统 - 回测模式")
    LOG.info("  区间: %s ~ %s", start_date, end_date or "最新")
    LOG.info("=" * 60)

    from backtest.engine import BacktestEngine
    from backtest.metrics import format_metrics_report

    engine = BacktestEngine(config)
    result = engine.run(start_date, end_date)

    if "error" in result:
        LOG.error("回测失败: %s", result["error"])
        print(f"\n[错误] {result['error']}")
        return result

    # 输出报告
    report = format_metrics_report(result["metrics"])
    print(report)

    if output_report:
        output_dir = config.get("general", {}).get("output_dir", "output")
        report_dir = os.path.join(_PROJECT_ROOT, output_dir, "reports")
        os.makedirs(report_dir, exist_ok=True)
        date_str = end_date or datetime.now().strftime("%Y%m%d")
        report_path = os.path.join(report_dir,
                                   f"backtest_report_{start_date}_{date_str}.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        LOG.info("回测报告已保存: %s", report_path)

        # 保存交易记录
        trades_path = os.path.join(report_dir,
                                   f"backtest_trades_{start_date}_{date_str}.csv")
        import pandas as pd
        trades_df = pd.DataFrame([{
            "ts_code": t.ts_code,
            "stock_name": t.stock_name,
            "buy_date": t.buy_date,
            "buy_price": round(t.buy_price, 2),
            "sell_date": t.sell_date,
            "sell_price": round(t.sell_price, 2) if t.sell_price else "",
            "pnl_pct": round(t.pnl_pct, 2),
            "hold_days": t.hold_days,
            "exit_reason": t.exit_reason,
            "primary_entry": t.primary_entry,
        } for t in result["trades"]])
        trades_df.to_csv(trades_path, index=False, encoding="utf-8-sig")
        LOG.info("交易记录已保存: %s", trades_path)

    return result


# ─────────────────────────────────────────────────────────────────
# 模式3: 训练LightGBM模型
# ─────────────────────────────────────────────────────────────────

def run_train(config: dict, start_date: str = "", end_date: str = ""):
    """训练LightGBM动态权重模型"""
    LOG.info("=" * 60)
    LOG.info("三层择时系统 - LightGBM训练模式")
    LOG.info("=" * 60)

    from data import pool_loader
    from trading.signal import SignalFusionEngine

    # 1. 加载股池
    pool_path = config.get("general", {}).get("stock_pool_path", "")
    pool_df = pool_loader.load_pool(pool_path)
    LOG.info("股池: %d 只股票", len(pool_df))

    # 2. 训练模型
    fusion_engine = SignalFusionEngine(config)
    if not start_date:
        # 默认使用近2年数据
        start_date = "20240101"
    fusion_engine.train_lgb_model(pool_df, start_date=start_date, end_date=end_date)

    LOG.info("模型训练完成")


# ─────────────────────────────────────────────────────────────────
# 模式4: 参数优化
# ─────────────────────────────────────────────────────────────────

def run_optimize(config: dict, start_date: str, end_date: str = ""):
    """参数优化模式"""
    LOG.info("=" * 60)
    LOG.info("三层择时系统 - 参数优化模式")
    LOG.info("  区间: %s ~ %s", start_date, end_date or "最新")
    LOG.info("=" * 60)

    from backtest.optimizer import ParamOptimizer

    bt_config = config.get("backtest", {})
    param_grid = bt_config.get("grid_search", {}).get("param_grid", {})

    if not param_grid:
        LOG.warning("param_grid 为空，使用默认参数网格")
        param_grid = {
            "entry_breakout_min_pct": [3.0, 4.0, 5.0],
            "entry_retrace_max_deviation": [-3.0, -4.0, -5.0],
            "exit_stop_loss": [-7.0, -8.0, -10.0],
            "position_score_70": [0.6, 0.7, 0.8],
        }

    optimizer = ParamOptimizer(config)
    result = optimizer.walk_forward(start_date, end_date, param_grid)

    # 输出结果
    agg = result.get("aggregated_metrics", {})
    stability = result.get("stability", {})

    print(f"\n{'='*60}")
    print("[Walk-Forward 优化结果]")
    print(f"{'='*60}")
    print(f"\n聚合绩效:")
    print(f"  年化收益: {agg.get('annual_return', 0):.1f}%")
    print(f"  夏普比: {agg.get('sharpe', 0):.2f}")
    print(f"  最大回撤: {agg.get('max_drawdown', 0):.1f}%")
    print(f"  胜率: {agg.get('win_rate', 0):.1f}%")
    print(f"  交易次数: {agg.get('total_trades', 0)}")

    print(f"\n参数稳定性:")
    print(f"  参数一致性: {stability.get('param_consistency', 0):.0%}")
    print(f"  夏普稳定度: {stability.get('sharpe_std', 0):.2f}")

    print(f"\n各窗口最优参数:")
    for i, w in enumerate(result.get("windows", [])):
        print(f"  窗口{i+1}: {w.get('best_params', {})} "
              f"→ 夏普={w.get('test_metrics', {}).get('sharpe', 0):.2f} "
              f"收益={w.get('test_metrics', {}).get('annual_return', 0):.1f}%")

    # 保存结果
    output_dir = config.get("general", {}).get("output_dir", "output")
    report_dir = os.path.join(_PROJECT_ROOT, output_dir, "reports")
    os.makedirs(report_dir, exist_ok=True)

    import json
    result_path = os.path.join(report_dir,
                               f"optimize_result_{start_date}_{end_date or 'latest'}.json")
    # 清理不可序列化内容
    clean_result = {
        "aggregated_metrics": agg,
        "stability": stability,
        "windows": [{
            "train_start": w.get("train_start", ""),
            "train_end": w.get("train_end", ""),
            "test_start": w.get("test_start", ""),
            "test_end": w.get("test_end", ""),
            "best_params": w.get("best_params", {}),
            "test_metrics": {k: v for k, v in w.get("test_metrics", {}).items()
                            if isinstance(v, (int, float, str))},
        } for w in result.get("windows", [])],
    }
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(clean_result, f, ensure_ascii=False, indent=2)
    LOG.info("优化结果已保存: %s", result_path)

    return result


# ─────────────────────────────────────────────────────────────────
# CLI入口
# ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="三层择时交易系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
运行示例:
  python main.py daily                    # 今日运行
  python main.py daily --date 20260717    # 指定日期
  python main.py backtest --start 20240101 --end 20260717
  python main.py train --start 20240101
  python main.py optimize --start 20240101 --end 20260717
        """,
    )

    parser.add_argument("mode", choices=["daily", "backtest", "train", "optimize"],
                        help="运行模式")
    parser.add_argument("--config", default="", help="配置文件路径")
    parser.add_argument("--date", default="", help="目标日期 (YYYYMMDD)")
    parser.add_argument("--start", default="", help="起始日期 (YYYYMMDD)")
    parser.add_argument("--end", default="", help="结束日期 (YYYYMMDD)")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")

    args = parser.parse_args()

    # 设置日志
    setup_logging(args.verbose)

    # 加载配置
    config = load_config(args.config)

    # 记录开始
    start_time = time.time()
    LOG.info("系统启动 | 模式: %s", args.mode)

    # 执行对应模式
    if args.mode == "daily":
        run_daily(config, args.date)
    elif args.mode == "backtest":
        start = args.start or config.get("backtest", {}).get("start_date", "20240101")
        end = args.end or config.get("backtest", {}).get("end_date", "")
        run_backtest(config, start, end)
    elif args.mode == "train":
        run_train(config, args.start, args.end)
    elif args.mode == "optimize":
        start = args.start or config.get("backtest", {}).get("start_date", "20240101")
        end = args.end or config.get("backtest", {}).get("end_date", "")
        run_optimize(config, start, end)

    elapsed = time.time() - start_time
    LOG.info("运行完成 | 耗时: %.1f 秒", elapsed)


if __name__ == "__main__":
    main()
