#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V6.0 - 主程序

每天盘后运行，输出主题主线分析报告
V6.0 改进：
  - 全主题百分位排名 (Relative Momentum)
  - DC热度数据集成 (Sentiment)
  - moneyflow资金流 (Capital)
  - limit_list_d涨停数据 (Sentiment/Leader)
  - top_list/top_inst龙虎榜 (Leader/Risk)
  - daily_basic换手率 (Risk)
"""
import os, sys, json, warnings
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
warnings.filterwarnings("ignore")

import config
import theme_builder as tb
import data_loader as dl
from trend import compute_trend_score, compute_momentum
from capital import compute_all_capital_scores
from sentiment import compute_sentiment_score
from persistence import compute_persistence_score
from lifecycle import identify_stage, stage_bonus
from leader import identify_leader
from risk import compute_risk_score
from continuation import compute_continuation_score, continuation_signal
from composite import compute_composite, trade_signal, confidence


def main(trade_date=None):
    print("=" * 70)
    print("  Theme Alpha Engine V6.0")
    print("  目标：寻找未来5~20日最可能成为市场主线的主题")
    print("=" * 70)

    if trade_date is None:
        trade_date = dl.get_last_trade_date()
    print(f"\n  交易日: {trade_date}")

    dt = datetime.strptime(trade_date, "%Y%m%d")
    start_date = (dt - timedelta(days=config.LOOKBACK_DAYS)).strftime("%Y%m%d")

    # ===== 第一步：加载主题池 =====
    print(f"\n[1/7] 加载主题池...")
    universe = tb.build_theme_universe()
    if not universe:
        print("[Error] 主题池为空")
        return []

    # ===== 第二步：加载日线数据 =====
    print(f"[2/7] 加载日线数据 ({start_date} ~ {trade_date})...")
    all_codes = list(set(sum(universe.values(), [])))
    daily = dl.load_daily(all_codes, start_date, trade_date)
    if daily.empty:
        print("[Error] 日线数据为空")
        return []
    print(f"      {len(daily)} 条记录, {daily['ts_code'].nunique()} 只股票")

    latest_day = daily["trade_date"].max()
    latest_daily = daily[daily["trade_date"] == latest_day]
    market_turnover = latest_daily["amount"].sum() / 1e8
    print(f"      最新交易日: {latest_day}, 全市场成交额: {market_turnover:.1f}亿")

    # ===== 第三步：加载辅助数据 =====
    print(f"[3/7] 加载辅助数据...")
    hs300 = dl.load_index("000300.SH", start_date, trade_date)
    index_ret = hs300.iloc[-1]["pct_chg"] if not hs300.empty else 0
    print(f"      沪深300收益: {index_ret:.2f}%")

    dc_hot = dl.load_dc_hot(trade_date)
    print(f"      DC热度: {'有' if not dc_hot.empty else '无'} ({len(dc_hot)} 条)")

    limit_df = dl.load_limit_list(trade_date)
    print(f"      涨停数据: {'有' if not limit_df.empty else '无'} ({len(limit_df)} 条)")

    top_df = dl.load_top_list(trade_date)
    top_inst = dl.load_top_inst(trade_date)
    print(f"      龙虎榜: {len(top_df)} 条, 机构: {len(top_inst)} 条")

    daily_basic = dl.load_daily_basic(trade_date)
    print(f"      daily_basic: {'有' if not daily_basic.empty else '无'}")

    moneyflow = dl.load_moneyflow_by_date(trade_date)
    print(f"      moneyflow: {'有' if not moneyflow.empty else '无'} ({len(moneyflow)} 条)")

    # ===== 第四步：计算全主题动量（用于百分位排名）=====
    print(f"[4/7] 计算全主题动量排名...")
    all_momentums = []
    for tname, codes in universe.items():
        r5, r10, r20, r40 = compute_momentum(daily, codes)
        all_momentums.append(r5 * 0.25 + r10 * 0.30 + r20 * 0.25 + r40 * 0.20)
    print(f"      {len(all_momentums)} 个主题动量计算完成")

    # ===== 第四步半：批量计算资金评分（跨主题百分位 + 非线性放大）=====
    print(f"[4.5/7] 批量计算资金评分...")
    capital_result = compute_all_capital_scores(daily, moneyflow, universe, market_turnover)
    print(f"        {len(capital_result)} 个主题资金评分完成")

    # ===== 第五步：逐个主题评分 =====
    print(f"[5/7] 主题评分计算中...")
    results = []
    theme_names = list(universe.keys())
    for i, tname in enumerate(theme_names):
        codes = universe[tname]
        if len(codes) < config.MIN_THEME_STOCKS:
            continue

        ts = compute_trend_score(daily, codes, all_momentums)
        # 从批量结果取 capital score 和子指标
        if tname in capital_result:
            cs, cap_metrics = capital_result[tname]
        else:
            cs, cap_metrics = 50.0, {}
        ss = compute_sentiment_score(daily, limit_df, dc_hot, codes, index_ret)
        ps = compute_persistence_score(daily, codes)
        rs = compute_risk_score(daily, codes, daily_basic, top_df)

        # 先识别龙头（延续评分需要龙头代码）
        ldr, ldr_score = identify_leader(daily, codes, top_df, top_inst)

        # 趋势延续评分：识别"强势延续"和"分歧买点"
        cont = compute_continuation_score(daily, codes, ldr)

        # 阶段判断（需 continuation 约束，避免"扩张+趋势走弱"矛盾）
        stage = identify_stage(ts, ss, cs, continuation=cont)
        lb = stage_bonus(stage)

        cscore = compute_composite(ts, cs, ss, ps, lb, ldr_score, rs, cont)
        sig = trade_signal(cscore, cs, ts, stage, cont)
        conf = confidence(cscore, ts, cs, cont)
        # 延续标签（需要真实 composite）
        cont_sig = continuation_signal(cont, cscore, stage)

        # 分歧买点标记：综合分确实低 + 延续分很高 + 阶段匹配
        is_divergence_buy = (cont >= config.WATCH_CONTINUATION
                             and cscore < config.WATCH_DIV_COMPOSITE
                             and stage in config.SB_STAGES)

        results.append({
            "theme": tname, "stage": stage, "leader": ldr or "",
            "trend_score": round(ts, 1), "capital_score": round(cs, 1),
            "cap_share": round(cap_metrics.get("market_share", 0) * 100, 2),
            "cap_accel": round(cap_metrics.get("acceleration", 0) * 100, 2),
            "cap_mflow": round(cap_metrics.get("mf_quality", 0) * 100, 1),
            "cap_conc": round(cap_metrics.get("concentration", 0) * 100, 1),
            "cap_persist": round(cap_metrics.get("persistence", 0) * 100, 1),
            "cap_rotation": round(cap_metrics.get("rotation", 0) * 100, 2),
            "sentiment_score": round(ss, 1), "persistence_score": round(ps, 1),
            "continuation_score": round(cont, 1),
            "risk_score": round(rs, 1), "lifecycle_score": lb,
            "leader_score": round(ldr_score, 1), "composite_score": round(cscore, 1),
            "confidence": round(conf, 1), "trade_signal": sig,
            "continuation_tag": cont_sig,
            "divergence_buy": "★" if is_divergence_buy else "",
        })

        if (i + 1) % 10 == 0:
            print(f"      进度: {i+1}/{len(theme_names)}")

    # ===== 第六步：排序输出 =====
    print(f"[6/7] 排序输出...")
    df = pd.DataFrame(results)
    df = df.sort_values("composite_score", ascending=False).reset_index(drop=True)

    df.to_json(config.OUTPUT_JSON, orient="records", force_ascii=False, indent=2)
    df.to_csv(config.OUTPUT_CSV, index=False, encoding="utf-8-sig")

    # ===== 第七步：打印报告 =====
    print(f"[7/7] 打印报告...")
    print(f"\n{'='*100}")
    print(f"  Theme Alpha V6.0 报告 — {trade_date}")
    print(f"{'='*100}")

    # TOP 15 主题（含延续分）
    top15 = df.head(15)
    print(f"\n  TOP 15 主题（按综合分排序）")
    print(f"  {'#':<3} {'主题':<16} {'综合':<6} {'趋势':<6} {'资金':<6} {'情绪':<6} {'延续':<6} {'阶段':<8} {'信号':<6} {'标记':<6} {'龙头'}")
    print(f"  {'-'*96}")
    for i, row in top15.iterrows():
        div_mark = row.get('divergence_buy', '')
        print(f"  {i+1:<3} {row['theme']:<16} {row['composite_score']:<6.1f} "
              f"{row['trend_score']:<6.1f} {row['capital_score']:<6.1f} "
              f"{row['sentiment_score']:<6.1f} {row['continuation_score']:<6.1f} "
              f"{row['stage']:<8} {row['trade_signal']:<6} {div_mark:<6} "
              f"{row['leader']}")

    # 分歧买点专区（综合分不高但延续分高）
    div_df = df[df.get('divergence_buy', '') == '★'].head(10)
    if not div_df.empty:
        print(f"\n  ★ 分歧买点专区（综合分一般，但延续概率高 — 分歧后大概率回归强势）")
        print(f"  {'#':<3} {'主题':<16} {'综合':<6} {'延续':<6} {'阶段':<8} {'龙头':<12} {'标记'}")
        print(f"  {'-'*70}")
        for _, row in div_df.iterrows():
            print(f"  {'':<3} {row['theme']:<16} {row['composite_score']:<6.1f} "
                  f"{row['continuation_score']:<6.1f} {row['stage']:<8} "
                  f"{row['leader']:<12} {row['trade_signal']}")

    # 延续排名 TOP 10（按延续分排序，找持续走强概率最高的）
    cont_top = df.sort_values('continuation_score', ascending=False).head(10)
    print(f"\n  延续概率 TOP 10（持续走强概率最高，不一定综合分最高）")
    print(f"  {'#':<3} {'主题':<16} {'延续':<6} {'综合':<6} {'阶段':<8} {'信号':<6} {'龙头'}")
    print(f"  {'-'*70}")
    for j, (_, row) in enumerate(cont_top.iterrows()):
        print(f"  {j+1:<3} {row['theme']:<16} {row['continuation_score']:<6.1f} "
              f"{row['composite_score']:<6.1f} {row['stage']:<8} "
              f"{row['trade_signal']:<6} {row['leader']}")

    # 资金分 TOP 10（含六维子指标）
    cap_top = df.sort_values('capital_score', ascending=False).head(10)
    print(f"\n  资金分 TOP 10（六维子指标明细）")
    print(f"  {'#':<3} {'主题':<14} {'资金':<5} {'占比':<6} {'加速':<6} {'质量':<5} {'集中':<5} {'持续':<5} {'轮动':<6}")
    print(f"  {'-'*68}")
    for j, (_, row) in enumerate(cap_top.iterrows()):
        print(f"  {j+1:<3} {row['theme']:<14} {row['capital_score']:<5.1f} "
              f"{row.get('cap_share',0):<6.2f} {row.get('cap_accel',0):<6.2f} "
              f"{row.get('cap_mflow',0):<5.1f} {row.get('cap_conc',0):<5.1f} "
              f"{row.get('cap_persist',0):<5.1f} {row.get('cap_rotation',0):<6.2f}")

    # 资金分分布（验证非线性放大效果）
    cap_scores = df['capital_score'].values
    print(f"\n  资金分分布: min={cap_scores.min():.1f} p25={np.percentile(cap_scores,25):.1f} "
          f"p50={np.percentile(cap_scores,50):.1f} p75={np.percentile(cap_scores,75):.1f} "
          f"max={cap_scores.max():.1f} std={cap_scores.std():.1f}")

    # 信号统计
    sig_counts = df["trade_signal"].value_counts()
    print(f"\n  信号分布: ", end="")
    for sig in ["强买", "关注", "持有", "回避"]:
        cnt = sig_counts.get(sig, 0)
        print(f"{sig}={cnt} ", end="")
    print(f"(总计 {len(df)})")

    # 阶段统计
    stage_counts = df["stage"].value_counts()
    print(f"  阶段分布: ", end="")
    for stg in ["启动", "扩张", "主升", "高潮", "衰退"]:
        cnt = stage_counts.get(stg, 0)
        print(f"{stg}={cnt} ", end="")
    print()

    # 延续标签统计
    if "continuation_tag" in df.columns:
        tag_counts = df["continuation_tag"].value_counts()
        print(f"  延续标签: ", end="")
        for tag in ["强势延续", "分歧买点", "观察等待", "趋势走弱"]:
            cnt = tag_counts.get(tag, 0)
            print(f"{tag}={cnt} ", end="")
        print()

    print(f"\n  结果已保存:")
    print(f"    {config.OUTPUT_JSON}")
    print(f"    {config.OUTPUT_CSV}")
    print(f"{'='*100}")

    return results


if __name__ == "__main__":
    main()
