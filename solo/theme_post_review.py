#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题轮动 - 盘后复盘
流程: 主题评分 → 状态机 → 龙头概率 → 启动股识别 → SQLite → Server酱

用法:
  python theme_post_review.py              # 复盘最近交易日
  python theme_post_review.py 20260529     # 指定日期
  python theme_post_review.py --no-push    # 不推送微信
"""
import os
import sys
import argparse
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from theme_rotation.config import CACHE_DIR
from theme_rotation.database import (
    init_rotation_db, load_portfolio, save_theme_daily,
    save_leader_daily, save_theme_state, save_daily_plan,
)
from theme_rotation.theme_state_machine import ThemeStateMachine, STATE_CN
from theme_rotation.leader_model import calc_theme_sector_score, score_theme_stocks, identify_starter
from theme_rotation.market_data import (
    get_last_trade_date, fetch_daily, fetch_daily_basic, fetch_limit_step,
)
from theme_rotation.notifier import push_review_report, push_daily_plan


def build_report(trade_date: str, ranked: list, plan: dict, all_starters: list) -> str:
    lines = [
        f"# 主题轮动复盘 {trade_date}",
        "",
        "## 一、明日作战结论",
        "",
    ]

    if plan.get("starter_name"):
        lines += [
            f"> **今日应做: 【{plan['starter_theme']}】主题的启动股 → {plan['starter_name']}**",
            f"> 启动概率: {plan.get('starter_prob', 0):.0f}%  |  主线: {plan.get('mainline_theme', '-')}  |  备选: {plan.get('backup_theme', '-')}",
            "",
        ]
    else:
        lines += ["> 暂无明确启动股，观望为主", ""]

    lines += ["## 二、主题轮动排名 TOP10", ""]
    lines.append("| 排名 | 主题 | 状态 | 强度 | 评分 | 动量 | 涨停数 |")
    lines.append("|------|------|------|------|------|------|--------|")
    for t in ranked[:10]:
        lines.append(
            f"| {t['rank']} | {t['theme_name']} | {t.get('state_cn', t['state'])} "
            f"| {t['strength']:.1f} | {t['score']:.1f} | {t['momentum']:+.1f} | {t['zt_count']} |"
        )

    lines += ["", "## 三、各主题启动股", ""]
    for s in all_starters[:8]:
        flag = "🔥" if s["theme_name"] == plan.get("mainline_theme") else ""
        lines.append(
            f"- {flag}**{s['theme_name']}** → {s['name']} ({s['ts_code']}) "
            f"启动{s['starter_prob']:.0f}% 龙头{s['leader_prob']:.0f}% "
            f"涨幅{s['pct_chg']:+.1f}%"
        )

    lines += ["", "## 四、主线确认逻辑", ""]
    lines += [
        "1. **强度排序**: 评分 + 0.6×动量 + 0.4×加速度",
        "2. **状态机**: 休眠→萌芽→主线→加速→分化→退潮",
        "3. **启动股**: 主题内最先涨停/最先强势（涨幅+换手）",
        "4. **明日首选**: 主线主题 × 最高启动概率",
        "",
        "---",
        f"*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
    ]
    return "\n".join(lines)


def build_plan_text(trade_date: str, plan: dict, ranked: list) -> str:
    lines = [
        f"## 明日作战计划 ({trade_date})",
        "",
    ]
    if plan.get("starter_name"):
        lines += [
            f"### 🎯 首选操作",
            f"**主题**: {plan['starter_theme']}",
            f"**启动股**: {plan['starter_name']} ({plan.get('starter_ts_code', '')})",
            f"**启动概率**: {plan.get('starter_prob', 0):.0f}%",
            "",
            f"**主线**: {plan.get('mainline_theme', '-')}",
            f"**备选**: {plan.get('backup_theme', '-')}",
            "",
        ]
    lines += ["### 📊 主题强度 TOP5", ""]
    for t in ranked[:5]:
        lines.append(
            f"{t['rank']}. {t['theme_name']} [{STATE_CN.get(t['state'], t['state'])}] "
            f"强度{t['strength']:.0f}"
        )
    return "\n".join(lines)


def run_review(trade_date: str = None, push: bool = True):
    print("=" * 60)
    print("主题轮动 - 盘后复盘")
    print("=" * 60)

    init_rotation_db()
    portfolio = load_portfolio()
    if not portfolio:
        print("portfolio 表为空，请先运行 theme_portfolio_strategy_cached.py")
        return

    trade_date = trade_date or get_last_trade_date()
    print(f"复盘日期: {trade_date}")
    print(f"成份股: {len(portfolio)} 只")

    # 按主题分组
    theme_groups = defaultdict(list)
    for s in portfolio:
        theme_groups[s["theme_name"]].append(s)

    ts_codes = list({s["ts_code"] for s in portfolio})

    print("\n[1/4] 获取行情数据...")
    daily_df = fetch_daily(trade_date, ts_codes)
    basic_df = fetch_daily_basic(trade_date, ts_codes)
    lb_map = fetch_limit_step(trade_date)

    daily_map = {}
    if not daily_df.empty:
        for _, row in daily_df.iterrows():
            daily_map[row["ts_code"]] = row.to_dict()

    if not basic_df.empty:
        for _, row in basic_df.iterrows():
            tc = row["ts_code"]
            if tc in daily_map:
                daily_map[tc]["turnover_rate"] = row.get("turnover_rate", 0)
            else:
                daily_map[tc] = {"turnover_rate": row.get("turnover_rate", 0), "pct_chg": 0}

    print(f"   日线: {len(daily_map)} 只, 连板: {len(lb_map)} 只")

    print("\n[2/4] 主题评分 + 状态机...")
    sm = ThemeStateMachine()
    theme_results = []
    all_leader_records = []
    all_starters = []

    for theme_name, stocks in theme_groups.items():
        scored = score_theme_stocks(stocks, daily_map, lb_map)

        daily_rows = [
            {"pct_chg": daily_map.get(s["ts_code"], {}).get("pct_chg", 0),
             "amount": daily_map.get(s["ts_code"], {}).get("amount", 0),
             "lb_height": lb_map.get(s["ts_code"], 0)}
            for s in stocks
        ]
        sector = calc_theme_sector_score(daily_rows)
        state_info = sm.update(theme_name, sector["score"])

        theme_results.append({
            **state_info,
            "zt_count": sector["zt_count"],
            "zt_ratio": sector["zt_ratio"],
            "max_lb": sector["max_lb"],
        })

        for r in scored:
            all_leader_records.append(r)
            if r.get("is_starter"):
                all_starters.append(r)

    ranked = sm.rank_themes(theme_results)
    mainline, backup = sm.pick_mainline(ranked)

    print("\n[3/4] 确定明日启动股...")
    # 在主线/备选主题中找最高 starter_prob
    priority_themes = [mainline, backup]
    priority_starters = [
        s for s in all_starters if s["theme_name"] in priority_themes
    ]
    if not priority_starters:
        priority_starters = all_starters

    best_starter = max(priority_starters, key=lambda x: x["starter_prob"]) if priority_starters else None

    plan = {
        "trade_date": trade_date,
        "mainline_theme": mainline,
        "backup_theme": backup,
        "starter_ts_code": best_starter["ts_code"] if best_starter else "",
        "starter_name": best_starter["name"] if best_starter else "",
        "starter_theme": best_starter["theme_name"] if best_starter else "",
        "starter_prob": best_starter["starter_prob"] if best_starter else 0,
        "ranking": [{"theme": t["theme_name"], "strength": t["strength"], "state": t["state"]} for t in ranked[:10]],
    }

    print("\n[4/4] 写入数据库...")
    save_theme_daily(trade_date, ranked)
    save_leader_daily(trade_date, all_leader_records)
    save_theme_state(ranked)
    save_daily_plan(trade_date, plan)

    # 保存文本报告
    report = build_report(trade_date, ranked, plan, all_starters)
    report_path = os.path.join(CACHE_DIR, f"theme_review_{trade_date}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n报告已保存: {report_path}")

    # 控制台输出
    print("\n" + "=" * 60)
    print("复盘结论")
    print("=" * 60)
    if best_starter:
        print(f"\n🎯 明日首选: 【{best_starter['theme_name']}】→ {best_starter['name']}")
        print(f"   代码: {best_starter['ts_code']}  启动概率: {best_starter['starter_prob']:.0f}%")
    print(f"\n主线: {mainline}  |  备选: {backup}")
    print("\n主题 TOP5:")
    for t in ranked[:5]:
        print(f"  {t['rank']}. {t['theme_name']:10s} [{STATE_CN.get(t['state'])}] 强度{t['strength']:.0f}")

    if push:
        push_review_report(trade_date, report)
        push_daily_plan(trade_date, build_plan_text(trade_date, plan, ranked))

    return plan


def main():
    parser = argparse.ArgumentParser(description="主题轮动盘后复盘")
    parser.add_argument("trade_date", nargs="?", help="交易日期 YYYYMMDD")
    parser.add_argument("--no-push", action="store_true", help="不推送微信")
    args = parser.parse_args()
    run_review(args.trade_date, push=not args.no_push)


if __name__ == "__main__":
    main()
