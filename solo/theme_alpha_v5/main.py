#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V5.0 - 主程序

每天盘后自动运行，输出主题主线分析报告
"""
import os, sys, json, warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BASE_DIR))
sys.path.insert(0, BASE_DIR)
warnings.filterwarnings("ignore")

import config
import theme_builder as tb
import data_loader as dl
from trend import compute_trend_score
from capital import compute_capital_score
from sentiment import compute_sentiment_score
from persistence import compute_persistence_score
from lifecycle import identify_stage, stage_bonus
from leader import identify_leader
from risk import compute_risk_score
from composite import compute_composite, trade_signal, confidence


def main(trade_date: str = None):
    print("=" * 70)
    print("  Theme Alpha Engine V5.0")
    print("  目标：寻找未来5~20日最可能成为市场主线的主题")
    print("=" * 70)

    if trade_date is None:
        trade_date = datetime.now().strftime("%Y%m%d")

    dt = datetime.strptime(trade_date, "%Y%m%d")
    start_date = (dt - timedelta(days=config.LOOKBACK_DAYS)).strftime("%Y%m%d")

    # ===== 第一步：加载主题池 =====
    print(f"\n[1/6] 加载主题池...")
    universe = tb.build_theme_universe()
    if not universe:
        print("[Error] 主题池为空，退出")
        return []

    # ===== 第二步：加载日线数据 =====
    print(f"[2/6] 加载日线数据 ({start_date} ~ {trade_date})...")
    all_codes = list(set(sum(universe.values(), [])))
    daily = dl.load_cache_daily(all_codes, start_date, trade_date)
    if daily.empty:
        print("[Error] 日线数据为空，退出")
        return []
    print(f"      共 {len(daily)} 条记录，{daily['ts_code'].nunique()} 只股票")

    # 计算全市场成交额
    latest_day = daily["trade_date"].max()
    latest_daily = daily[daily["trade_date"] == latest_day]
    market_turnover = latest_daily["amount"].sum() / 1e8  # 亿元

    # 指数数据
    hs300 = dl.load_index_data("000300.SH", start_date, trade_date)
    index_ret = hs300.iloc[-1]["pct_chg"] if not hs300.empty else 0

    # 涨停数据
    limit_df = dl.load_limit_list(trade_date)

    # ===== 第三步：逐个主题评分 =====
    print(f"[3/6] 主题评分计算中...")
    results = []
    theme_names = list(universe.keys())
    for i, tname in enumerate(theme_names):
        codes = universe[tname]
        if len(codes) < config.MIN_THEME_STOCKS:
            continue

        # 各维度评分
        ts = compute_trend_score(daily, codes)
        cs = compute_capital_score(daily, pd.DataFrame(), codes, market_turnover)
        ss = compute_sentiment_score(daily, limit_df, codes, index_ret)
        ps = compute_persistence_score(daily, codes)
        rs = compute_risk_score(daily, codes)

        # 生命周期
        stage = identify_stage(ts, ss, cs)
        lb = stage_bonus(stage)

        # 龙头
        ldr, ldr_score = identify_leader(daily, codes, limit_df)

        # 综合
        cscore = compute_composite(ts, cs, ss, ps, lb, ldr_score, rs)
        sig = trade_signal(cscore, cs, ts, stage)
        conf = confidence(cscore, ts, cs)

        results.append({
            "theme": tname,
            "trend_score": round(ts, 1),
            "capital_score": round(cs, 1),
            "sentiment_score": round(ss, 1),
            "persistence_score": round(ps, 1),
            "risk_score": round(rs, 1),
            "lifecycle_score": lb,
            "leader_score": round(ldr_score, 1),
            "composite_score": round(cscore, 1),
            "confidence": round(conf, 1),
            "stage": stage,
            "leader": ldr or "",
            "trade_signal": sig,
        })

        if (i + 1) % 10 == 0:
            print(f"      进度: {i+1}/{len(theme_names)}")

    # ===== 第四步：排序输出 =====
    print(f"[4/6] 排序输出...")
    df = pd.DataFrame(results)
    df = df.sort_values("composite_score", ascending=False).reset_index(drop=True)

    # 保存
    out_json = os.path.join(config.CACHE_DIR, "theme_alpha_v5_result.json")
    out_csv = os.path.join(config.CACHE_DIR, "theme_alpha_v5_result.csv")
    df.to_json(out_json, orient="records", force_ascii=False, indent=2)
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")

    # ===== 第五步：打印报告 =====
    print(f"[5/6] 打印报告...")
    print(f"\n{'='*70}")
    print(f"  Theme Alpha V5.0 报告 — {trade_date}")
    print(f"{'='*70}")

    top10 = df.head(10)
    print(f"\n  TOP 10 主题")
    print(f"  {'排名':<4} {'主题名称':<20} {'综合':<6} {'趋势':<6} {'资金':<6} {'情绪':<6} {'阶段':<12} {'信号':<12}")
    print(f"  {'-'*66}")
    for i, row in top10.iterrows():
        print(f"  {i+1:<4} {row['theme']:<20} {row['composite_score']:<6.1f} "
              f"{row['trend_score']:<6.1f} {row['capital_score']:<6.1f} "
              f"{row['sentiment_score']:<6.1f} {row['stage']:<12} {row['trade_signal']:<12}")

    # 按信号统计
    strong_buy = df[df["trade_signal"] == "Strong Buy"]
    watch = df[df["trade_signal"] == "Watch"]
    hold = df[df["trade_signal"] == "Hold"]
    avoid = df[df["trade_signal"] == "Avoid"]

    print(f"\n  信号分布")
    print(f"  {'信号':<16} {'数量':<6}")
    print(f"  {'-'*22}")
    print(f"  {'Strong Buy':<16} {len(strong_buy):<6}")
    print(f"  {'Watch':<16} {len(watch):<6}")
    print(f"  {'Hold':<16} {len(hold):<6}")
    print(f"  {'Avoid':<16} {len(avoid):<6}")
    print(f"  {'-'*22}")
    print(f"  {'总计':<16} {len(df):<6}")

    print(f"\n[6/6] 完成！结果已保存到:")
    print(f"      {out_json}")
    print(f"      {out_csv}")
    print(f"{'='*70}")

    return results


if __name__ == "__main__":
    main()
