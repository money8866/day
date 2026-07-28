"""
主线龙头 + 首次回踩强支撑 选股系统 — 每日盘后运行入口

使用方法：
    python run_pullback_strategy.py [--date YYYYMMDD] [--output DIR]

功能：
  1. 运行完整 A→B→C 三层选股逻辑
  2. 输出 Markdown 表格到控制台
  3. 保存 CSV 文件到 output 目录
  4. 打印综合统计信息
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path 中（使 mainline_pullback 可导入）
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pandas as pd

from mainline_pullback.config import get_config, Config
from mainline_pullback.data_loader import get_last_trade_date, load_local_data
from mainline_pullback.strategy_engine import StrategyEngine, StockResult


# ──────────────────────────────────────────────
# 日志配置
# ──────────────────────────────────────────────

def setup_logging(verbose: bool = False) -> None:
    """配置日志输出"""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


# ──────────────────────────────────────────────
# 输出模块
# ──────────────────────────────────────────────

def print_markdown_table(results: list[StockResult]) -> None:
    """打印漂亮的 Markdown 表格到控制台"""
    if not results:
        print("\n⚠️  无符合选股条件的股票。")
        return

    # 只显示 pass_c 的
    passed = [r for r in results if r.pass_c]
    if not passed:
        print("\n⚠️  无符合 C层首次回踩条件的股票。")
        # 仍然显示 B层候选供参考
        print(f"\n[B层候选] {len(results)} 只股票通过主升浪筛选，但未通过回踩检测。")
        return

    print(f"\n{'='*100}")
    print(f"📊 【主线龙头 + 首次回踩】选股结果  ({passed[0].trade_date})")
    print(f"共 {len(passed)} 只股票入选, 按综合评分降序")
    print(f"{'='*100}\n")

    # ── 表头 ──
    headers = [
        "排名", "代码", "名称", "收盘价", "涨跌幅%", "成交额亿",
        "主升涨幅%", "涨停数", "回撤%", "支撑MA",
        "距MA%", "缩量比", "综合分", "盈亏比",
    ]
    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "|" + "|".join(["---"] * len(headers)) + "|"

    print(header_line)
    print(sep_line)

    # ── 数据行 ──
    for rank, r in enumerate(passed, 1):
        row = [
            str(rank),
            f"`{r.ts_code}`",
            r.name,
            f"{r.close:.2f}",
            f"{r.pct_chg:+.2f}",
            f"{r.amount:.2f}",
            f"{r.wave_gain_pct:.1f}",
            str(r.limit_up_count),
            f"{r.pullback_from_high:.1f}",
            f"MA{r.support_ma}",
            f"{r.dist_to_ma_pct:+.2f}",
            f"{r.vol_shrink_ratio:.2f}",
            f"**{r.score:.1f}**",
            f"{r.risk_reward_ratio:.1f}",
        ]
        line = "| " + " | ".join(row) + " |"
        print(line)

    print(f"\n{'='*100}\n")

    # ── 操作建议区 ──
    print("📋 **操作建议**")
    print()
    for r in passed[:10]:  # 只显示TOP10
        action = ""
        if r.risk_reward_ratio >= 2.0:
            action = "✅ 较优机会"
        elif r.risk_reward_ratio >= 1.5:
            action = "👍 可关注"
        else:
            action = "👀 观察"

        print(f"  - **{r.name}** ({r.ts_code})")
        print(f"    {action}")
        print(f"    入场: {r.entry_price:.2f} | 止损: {r.stop_loss:.2f} | 止盈: {r.take_profit:.2f} | 盈亏比: {r.risk_reward_ratio:.1f}")
        print()


def save_to_csv(results: list[StockResult], output_path: str) -> None:
    """保存结果为 CSV 文件"""
    passed = [r for r in results if r.pass_c]
    if not passed:
        return

    fields = [
        "ts_code", "name", "trade_date", "close", "pct_chg", "amount",
        "wave_gain_pct", "limit_up_count", "pullback_from_high",
        "support_ma", "support_price", "dist_to_ma_pct",
        "vol_shrink_ratio", "vol_peak",
        "score", "wave_score", "pullback_score", "shrink_score",
        "support_score", "volume_ratio_score",
        "risk_reward_ratio", "entry_price", "stop_loss", "take_profit",
    ]

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in passed:
            writer.writerow(r.__dict__)

    print(f"💾 CSV 已保存: {output_path}")


def print_summary(results: list[StockResult], trade_date: str) -> None:
    """打印综合统计摘要"""
    passed = [r for r in results if r.pass_c]
    print()
    print("📈 **统计摘要**")
    print(f"  - 交易日: {trade_date}")
    print(f"  - A层基础过滤候选: {len(results) + len(passed) if results else 0} 只")
    print(f"  - B层主升浪动量通过: {len(results)} 只")
    print(f"  - C层首次回踩信号: {len(passed)} 只")
    if passed:
        scores = [r.score for r in passed]
        print(f"  - 综合评分均值: {sum(scores) / len(scores):.1f}")
        print(f"  - 评分区间: {min(scores):.1f} ~ {max(scores):.1f}")
        rr = [r.risk_reward_ratio for r in passed if r.risk_reward_ratio > 0]
        if rr:
            print(f"  - 盈亏比均值: {sum(rr) / len(rr):.1f}")
        print()


# ──────────────────────────────────────────────
# 命令行入口
# ──────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="主线龙头 + 首次回踩强支撑 量化选股系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python run_pullback_strategy.py                          # 自动识别最新交易日
  python run_pullback_strategy.py --date 20260724          # 指定交易日
  python run_pullback_strategy.py --date 20260724 --output D:/mystock/reports
  python run_pullback_strategy.py --verbose                # 详细日志
        """,
    )
    parser.add_argument("--date", type=str, default="", help="交易日 YYYYMMDD，留空自动识别")
    parser.add_argument("--output", type=str, default="", help="输出目录（默认: mainline_pullback/output）")
    parser.add_argument("--verbose", action="store_true", help="详细日志（DEBUG 级别）")
    parser.add_argument("--no-csv", action="store_true", help="不输出 CSV 文件")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(verbose=args.verbose)

    # 确定交易日
    trade_date = args.date or get_last_trade_date()
    logger = logging.getLogger("mainline_pullback")
    logger.info("交易日: %s", trade_date)

    # 运行策略引擎
    engine = StrategyEngine()
    results = engine.run(trade_date=trade_date)

    # ── 输出 ──
    # Markdown 表格
    print_markdown_table(results)

    # CSV
    if not args.no_csv:
        cfg = get_config()
        output_dir = args.output or os.path.join(cfg.path.solo_dir, "mainline_pullback", "output")
        os.makedirs(output_dir, exist_ok=True)
        csv_path = os.path.join(output_dir, f"pullback_signals_{trade_date}.csv")
        save_to_csv(results, csv_path)

    # 统计摘要
    print_summary(results, trade_date)

    logger.info("✅ 选股完成！")


if __name__ == "__main__":
    main()
