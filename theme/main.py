#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
盘后题材轮动引擎
================
每日盘后运行，输出：
  1. theme_rank.csv     — 题材排名+阶段
  2. leaders.csv        — 龙头/中军/补涨
  3. tomorrow_watch.csv — 次日观察池（加速题材）
  4. report_{date}.md   — 复盘报告
  5. rotation_matrix.txt — 轮动路径
"""
import argparse
import csv
import os
from datetime import datetime, timedelta

from config import OUTPUT_DIR, TOP_N
from db import init_db, save_top10_to_portfolio_db
from scorer import calc_all_theme_scores
from stage import detect_stages_for_all
from leader import identify_leaders
from rotation import build_rotation, select_tomorrow_watch, compute_rotation_matrix
from eastmoney import update_hot_track_block


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def write_csv(path, headers, rows):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)
    print(f"  {path}")


def generate_report(trade_date, scored_themes, stages, leaders, watch, rotations):
    stage_map = {s["theme_name"]: s["stage"] for s in stages}
    leader_map = {l["theme_name"]: l for l in leaders}

    lines = []
    lines.append("# 盘后题材轮动复盘报告")
    lines.append(f"**交易日期**: {trade_date}")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append("---")
    lines.append("")

    top = sorted(scored_themes, key=lambda x: x["score"], reverse=True)

    # TOP 10
    lines.append("## 一、题材强度排名 TOP10")
    lines.append("")
    lines.append("| 排名 | 题材 | 评分 | 阶段 | 龙头 | 中军 | 弹性补涨 |")
    lines.append("|------|------|------|------|------|------|------|")
    for i, item in enumerate(top[:10]):
        stage = stage_map.get(item["theme_name"], "震荡")
        ldr = leader_map.get(item["theme_name"], {})
        lines.append(f"| {i+1} | {item['theme_name']} | {item['score']:.1f} | {stage} | {ldr.get('leader','')} | {ldr.get('core','')} | {ldr.get('supplement','')} |")
    lines.append("")

    # 11-20
    if len(top) > 10:
        lines.append("## 二、题材强度排名 11-20")
        lines.append("")
        lines.append("| 排名 | 题材 | 评分 | 阶段 |")
        lines.append("|------|------|------|------|")
        for i, item in enumerate(top[10:20], start=11):
            lines.append(f"| {i} | {item['theme_name']} | {item['score']:.1f} | {stage_map.get(item['theme_name'], '震荡')} |")
        lines.append("")

    # Watch pool
    if watch:
        lines.append("## 三、次日观察方向（加速题材）")
        lines.append("")
        lines.append("| 题材 | 评分 | 加速度 |")
        lines.append("|------|------|--------|")
        for w in watch:
            lines.append(f"| {w['theme_name']} | {w['score']:.1f} | +{w['accelerate']:.1f} |")
        lines.append("")

    # Rotation matrix
    if rotations:
        lines.append("## 四、轮动路径（近30天）")
        lines.append("")
        for (out_t, in_t), cnt in rotations.most_common(10):
            lines.append(f"- {out_t} → {in_t}: {cnt}次")
        lines.append("")

    lines.append("---")
    lines.append("*免责声明：仅供参考，不构成投资建议。*")

    path = os.path.join(OUTPUT_DIR, f"report_{trade_date}.md")
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  {path}")
    return "\n".join(lines)


def _get_prev_trade_date():
    """自动获取上一个交易日"""
    import tushare as ts
    from config import TS_TOKEN
    pro = ts.pro_api(TS_TOKEN)
    today = datetime.now().strftime("%Y%m%d")
    cal = pro.trade_cal(exchange="SSE", start_date=(datetime.now()-timedelta(days=10)).strftime("%Y%m%d"), end_date=today)
    if cal is not None and not cal.empty:
        trading_days = cal[cal["is_open"] == 1]["cal_date"].sort_values().tolist()
        if len(trading_days) >= 2 and trading_days[-1] == today:
            return trading_days[-2]
        if len(trading_days) >= 1 and trading_days[-1] != today:
            return trading_days[-1]
    return (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")


def run(trade_date=None):
    if trade_date is None:
        trade_date = _get_prev_trade_date()

    print("=" * 55)
    print("  盘后题材轮动引擎")
    print(f"  日期: {trade_date}")
    print("=" * 55)

    # 1. Init DB
    print("\n[1/5] 初始化数据库...")
    init_db()
    print("  DB ready")

    # 2. 从 theme_portfolio.db 读取题材 + 计算评分
    print("\n[2/5] 计算题材评分...")
    scored_themes = calc_all_theme_scores(trade_date)
    if not scored_themes:
        print("评分失败，终止")
        return

    # 3. 生命周期 + 龙头
    print("\n[3/5] 识别生命周期 & 龙头...")
    stages = detect_stages_for_all(scored_themes)
    leaders = identify_leaders(trade_date, scored_themes)
    print(f"  {len(leaders)} 个题材完成龙头识别")

    # 写入东方财富热点跟踪板块（只写高潮/主升/启动 + TOP5中的震荡）
    if leaders:
        stage_map = {s["theme_name"]: s["stage"] for s in stages}
        scored_names = [t["theme_name"] for t in sorted(scored_themes, key=lambda x: x["score"], reverse=True)]
        top5_names_in_order = set(scored_names[:5])

        all_codes = set()
        for l in leaders:
            stage = stage_map.get(l["theme_name"], "震荡")
            include = (stage in ("高潮", "主升", "启动")) or (l["theme_name"] in top5_names_in_order)
            if not include:
                continue
            if l.get("leader_code"):
                all_codes.add(l["leader_code"])
            if l.get("core_code"):
                all_codes.add(l["core_code"])
            for c in l.get("supp_codes", []):
                if c:
                    all_codes.add(c)

        print(f"  非震荡+TOP5震荡: {len(all_codes)} 只个股写入东方财富...")
        update_hot_track_block(list(all_codes))

    # 写入TOP10到theme_portfolio.db
    print("  写入TOP10板块及个股到theme_portfolio.db...")
    save_top10_to_portfolio_db(trade_date, scored_themes, stages, leaders)

    # 4. 轮动 + 观察池
    print("\n[4/5] 轮动图谱 & 观察池...")
    build_rotation(trade_date, scored_themes, top_n=TOP_N)
    watch = select_tomorrow_watch(scored_themes, top_n=10)
    rot_matrix = compute_rotation_matrix(lookback_days=30)
    print(f"  轮动已记录, {len(watch)} 个观察方向")

    # 5. 输出
    print("\n[5/5] 输出文件...")
    top = sorted(scored_themes, key=lambda x: x["score"], reverse=True)

    write_csv(
        os.path.join(OUTPUT_DIR, "theme_rank.csv"),
        ["rank", "theme_name", "score", "stage"],
        [(i+1, t["theme_name"], f"{t['score']:.2f}", stages[i]["stage"]) for i, t in enumerate(top[:TOP_N])]
    )
    if leaders:
        write_csv(
            os.path.join(OUTPUT_DIR, "leaders.csv"),
            ["theme", "leader", "core", "弹性补涨"],
            [(l["theme_name"], l["leader"], l["core"], l["supplement"]) for l in leaders]
        )
    if watch:
        write_csv(
            os.path.join(OUTPUT_DIR, "tomorrow_watch.csv"),
            ["theme_name", "score", "accelerate"],
            [(w["theme_name"], f"{w['score']:.2f}", f"+{w['accelerate']:.2f}") for w in watch]
        )
    if rot_matrix:
        path = os.path.join(OUTPUT_DIR, "rotation_matrix.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("轮动路径（近30天）\n" + "=" * 30 + "\n")
            for (out_t, in_t), cnt in rot_matrix.most_common(20):
                f.write(f"  {out_t} -> {in_t}: {cnt}次\n")
        print(f"  {path}")

    generate_report(trade_date, scored_themes, stages, leaders, watch, rot_matrix)

    print("\n" + "=" * 55)
    print("  全部完成！")
    print(f"  输出目录: {OUTPUT_DIR}")
    print("=" * 55)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="盘后题材轮动引擎")
    parser.add_argument("date", nargs="?", help="交易日期 YYYYMMDD，默认今天")
    args = parser.parse_args()
    run(trade_date=args.date)
