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
from market_regime_v3.wechat_push import send_pushplus


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
    if passed[0].market_regime:
        print(f"📌 大盘状态: {passed[0].market_regime} | 市场偏好: {passed[0].market_preference or '均衡'}")
    print(f"共 {len(passed)} 只股票入选, 按综合评分降序")
    print(f"{'='*100}\n")

    # ── 表头 ──
    headers = [
        "排名", "代码", "名称", "风格", "适配",
        "收盘价", "涨跌幅%",
        "主升涨幅%", "涨停数", "回撤%", "支撑MA",
        "距MA%", "缩量比", "综合分", "盈亏比",
        "买点信号", "Ready分", "买入区间",
    ]
    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "|" + "|".join(["---"] * len(headers)) + "|"

    print(header_line)
    print(sep_line)

    # ── 数据行 ──
    style_icon = {'防御型': '🛡️', '高弹性': '🚀', '均衡型': '⚖️'}
    hint_icon = {'推荐优先': '✅', '谨慎': '⚠️', '中性': '➖'}
    for rank, r in enumerate(passed, 1):
        buy_range = f"{r.buy_price_low:.2f}~{r.buy_price_high:.2f}" if r.buy_price_low else "-"
        row = [
            str(rank),
            f"`{r.ts_code}`",
            r.name,
            f"{style_icon.get(r.stock_style, '')}{r.stock_style}",
            f"{hint_icon.get(r.style_hint, '')}{r.style_hint}",
            f"{r.close:.2f}",
            f"{r.pct_chg:+.2f}",
            f"{r.wave_gain_pct:.1f}",
            str(r.limit_up_count),
            f"{r.pullback_from_high:.1f}",
            f"MA{r.support_ma}",
            f"{r.dist_to_ma_pct:+.2f}",
            f"{r.vol_shrink_ratio:.2f}",
            f"**{r.score:.1f}**",
            f"{r.risk_reward_ratio:.1f}",
            r.buy_signal,
            f"{r.buy_readiness:.0f}",
            buy_range,
        ]
        line = "| " + " | ".join(row) + " |"
        print(line)

    print(f"\n{'='*100}\n")

    # ── 操作建议区 ──
    print("📋 **操作建议（按买入 readiness 排序）**")
    print()
    for r in sorted(passed, key=lambda x: x.buy_readiness, reverse=True)[:10]:
        signal_icon = {"READY": "🔴 可买入", "WATCH": "🟡 观察", "WAIT": "⚪ 等待"}.get(r.buy_signal, "⚪")
        kdj_info = f"KDJ(J={r.kdj_j:.0f} {'↑' if r.kdj_turn == 'up' else '↓'})" if r.kdj_j else ""
        rsi_info = f"RSI={r.rsi_6:.0f}" if r.rsi_6 else ""

        style_tag = {'防御型': '🛡️防御', '高弹性': '🚀高弹性', '均衡型': '⚖️均衡'}.get(r.stock_style, r.stock_style)
        hint_tag = {'推荐优先': '✅推荐', '谨慎': '⚠️谨慎', '中性': '➖'}.get(r.style_hint, r.style_hint)
        print(f"  - **{r.name}** ({r.ts_code}) [{style_tag} | {hint_tag}]")
        print(f"    {signal_icon} | readiness={r.buy_readiness:.0f} | {kdj_info} {rsi_info} | {r.candle_pattern}")
        print(f"    买入区: {r.buy_price_low:.2f}~{r.buy_price_high:.2f} | 止损: {r.stop_loss:.2f} | 止盈: {r.take_profit:.2f} | 盈亏比: {r.risk_reward_ratio:.1f}")
        print()


def save_to_csv(results: list[StockResult], output_path: str) -> None:
    """保存结果为 CSV 文件"""
    passed = [r for r in results if r.pass_c]
    if not passed:
        return

    fields = [
        "ts_code", "name", "trade_date", "close", "pct_chg", "amount",
        "stock_style", "style_hint", "market_regime", "market_preference",
        "wave_gain_pct", "limit_up_count", "pullback_from_high",
        "support_ma", "support_price", "dist_to_ma_pct",
        "vol_shrink_ratio", "vol_peak",
        "score", "wave_score", "pullback_score", "shrink_score",
        "support_score", "volume_ratio_score",
        "risk_reward_ratio", "entry_price", "stop_loss", "take_profit",
        "buy_signal", "buy_readiness", "buy_price_low", "buy_price_high",
        "kdj_j", "kdj_turn", "rsi_6", "candle_pattern", "consecutive_down",
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
    print(f"  - C层（含主升浪动量+首次回踩）通过: {len(passed)} 只")
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
    parser.add_argument("--push", action="store_true", help="推送结果到微信（PushPlus）")
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

    # ── 微信推送 ──
    if args.push:
        push_results_to_wechat(results, trade_date)

    logger.info("✅ 选股完成！")


def push_results_to_wechat(results: list[StockResult], trade_date: str) -> None:
    """推送选股结果到微信"""
    passed = [r for r in results if r.pass_c]
    if not passed:
        send_pushplus(f"# 主线龙头首次回踩选股 — {trade_date}\n\n暂无符合条件标的。",
                       title=f"回踩选股 {trade_date} — 无信号")
        return

    lines = []
    lines.append(f"# Gemini 回踩算法 — {trade_date}")
    lines.append("")

    # 大盘状态
    r0 = passed[0]
    lines.append(f"**大盘**: {r0.market_regime} | **偏好**: {r0.market_preference}")
    lines.append(f"**入选**: {len(passed)} 只 | **均分**: {sum(r.score for r in passed)/len(passed):.1f}")
    lines.append("")

    # TOP 10
    lines.append("## TOP 10 精选")
    lines.append("|#|名称|风格|适配|信号|Ready|评分|盈亏比|买入区间|")
    lines.append("|--|----|----|----|----|----|----|----|--------|")
    style_tag = {'防御型': '🛡️', '高弹性': '🚀', '均衡型': '⚖️'}
    hint_icon = {'推荐优先': '✅', '谨慎': '⚠️', '中性': '➖'}
    for i, r in enumerate(passed[:10], 1):
        buy_range = f"{r.buy_price_low:.2f}~{r.buy_price_high:.2f}" if r.buy_price_low else "-"
        lines.append(
            f"|{i}|{r.name}|{style_tag.get(r.stock_style,'')}{r.stock_style}"
            f"|{hint_icon.get(r.style_hint,'')}{r.style_hint}"
            f"|{r.buy_signal}|{r.buy_readiness:.0f}"
            f"|{r.score:.1f}|{r.risk_reward_ratio:.1f}|{buy_range}|"
        )
    lines.append("")

    # 分类统计
    lines.append("## 风格分布")
    def_cnt = sum(1 for r in passed if r.stock_style == '防御型')
    agg_cnt = sum(1 for r in passed if r.stock_style == '高弹性')
    bal_cnt = sum(1 for r in passed if r.stock_style == '均衡型')
    rec_cnt = sum(1 for r in passed if r.style_hint == '推荐优先')
    cau_cnt = sum(1 for r in passed if r.style_hint == '谨慎')
    lines.append(f"- 🛡️ 防御型 {def_cnt} 只 | 🚀 高弹性 {agg_cnt} 只 | ⚖️ 均衡型 {bal_cnt} 只")
    lines.append(f"- ✅ 推荐优先 {rec_cnt} 只 | ⚠️ 谨慎 {cau_cnt} 只")
    lines.append("")

    # READY 信号
    ready = [r for r in passed if r.buy_signal == 'READY']
    if ready:
        lines.append("## 🔴 可买入（READY）")
        for r in ready:
            lines.append(f"- **{r.name}** {r.ts_code} | Ready={r.buy_readiness:.0f} | "
                         f"买入区 {r.buy_price_low:.2f}~{r.buy_price_high:.2f} | "
                         f"止损 {r.stop_loss:.2f}")
        lines.append("")

    lines.append("---")
    lines.append(f"*Pullback Strategy · {trade_date} 自动推送*")

    send_pushplus("\n".join(lines), title=f"Gemini 回踩算法 {trade_date} ({len(passed)}只)")


if __name__ == "__main__":
    main()
