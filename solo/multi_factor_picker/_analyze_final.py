# -*- coding: utf-8 -*-
"""
深入分析：不同评分阈值下的信号质量和数量
目标：找到确保"每天至少一只"的最优条件
"""
import pandas as pd
import numpy as np

df = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\sideways_analysis.csv')
print(f"总样本: {len(df)}只（沪深300池）")
print()

# 基础条件：回调3-10% + 一波20-60%
base_mask = (df['pullback_pct'] >= 3) & (df['pullback_pct'] < 10) & (df['wave1_gain'] >= 20) & (df['wave1_gain'] < 60)
base_df = df[base_mask]
print(f"基础条件（回调3-10% + 一波20-60%）: {len(base_df)}只")
print()

# 不同评分阈值测试
print("=" * 80)
print(f"{'评分阈值':<10} {'数量':>4} {'+20%胜率':>8} {'+30%胜率':>8} {'止损率':>7} {'正收益':>7} {'均最大涨':>8}")
print("-" * 80)

for score_min in [30, 29, 28, 27, 26, 25, 24, 23, 22, 20]:
    sub = base_df[base_df['score'] >= score_min]
    n = len(sub)
    if n == 0:
        continue
    hit20 = sub['hit_20'].mean() * 100
    hit30 = sub['hit_30'].mean() * 100
    stop = sub['hit_stop'].mean() * 100
    win = (sub['final_gain'] > 0).mean() * 100
    avg_max = sub['max_gain'].mean()
    
    # 估算全量合格股日均
    # 41只样本 ≈ 沪深300池(200只)的所有强势横盘信号
    # 合格股池946只，约4.7倍
    est_daily = n / 41 * 1.03 * 4.7  # 1.03是沪深300池日均
    print(f"≥{score_min}分{'':<5} {n:>4} {hit20:>7.1f}% {hit30:>7.1f}% {stop:>6.1f}% {win:>6.1f}% {avg_max:>7.1f}%  | 推算日均约{est_daily:.2f}只")

print()
print("=" * 80)
print("其他维度放宽测试（评分≥25为基准）:")
print()

base_score25 = base_df[base_df['score'] >= 25]
print(f"基准（评分≥25+回调3-10%+一波20-60%）: {len(base_score25)}只")

# 回调下限降到2%
pb2 = df[(df['pullback_pct'] >= 2) & (df['pullback_pct'] < 10) & (df['wave1_gain'] >= 20) & (df['wave1_gain'] < 60) & (df['score'] >= 25)]
print(f"回调下限2%: {len(pb2)}只 (+{len(pb2)-len(base_score25)})")

# 一波下限降到15%
s15 = df[(df['pullback_pct'] >= 3) & (df['pullback_pct'] < 10) & (df['wave1_gain'] >= 15) & (df['wave1_gain'] < 60) & (df['score'] >= 25)]
print(f"一波下限15%: {len(s15)}只 (+{len(s15)-len(base_score25)})")

# 一波上限提到80%
s80 = df[(df['pullback_pct'] >= 3) & (df['pullback_pct'] < 10) & (df['wave1_gain'] >= 20) & (df['wave1_gain'] < 80) & (df['score'] >= 25)]
print(f"一波上限80%: {len(s80)}只 (+{len(s80)-len(base_score25)})")

# 回调上限提到12%
pb12 = df[(df['pullback_pct'] >= 3) & (df['pullback_pct'] < 12) & (df['wave1_gain'] >= 20) & (df['wave1_gain'] < 60) & (df['score'] >= 25)]
print(f"回调上限12%: {len(pb12)}只 (+{len(pb12)-len(base_score25)})")

# 同时放宽：评分24 + 回调2-10% + 一波15-70%
combo = df[(df['pullback_pct'] >= 2) & (df['pullback_pct'] < 10) & (df['wave1_gain'] >= 15) & (df['wave1_gain'] < 70) & (df['score'] >= 24)]
print(f"\n组合放宽（评分≥24+回调2-10%+一波15-70%）: {len(combo)}只")
if len(combo) > 0:
    print(f"  +20%胜率: {combo['hit_20'].mean()*100:.1f}%")
    print(f"  止损率: {combo['hit_stop'].mean()*100:.1f}%")
    est_daily = len(combo) / 41 * 1.03 * 4.7
    print(f"  推算日均约: {est_daily:.2f}只")

print()
print("=" * 80)
print("推荐方案对比:")
print()

plans = [
    ("保守方案: 评分≥26 + 回调3-10% + 一波20-60%", 26, 3, 10, 20, 60),
    ("均衡方案: 评分≥25 + 回调3-10% + 一波20-60%", 25, 3, 10, 20, 60),
    ("积极方案: 评分≥24 + 回调3-10% + 一波20-65%", 24, 3, 10, 20, 65),
    ("激进方案: 评分≥23 + 回调2-10% + 一波15-70%", 23, 2, 10, 15, 70),
]

for name, smin, pbmin, pbmax, smin_w, smax_w in plans:
    mask = ((df['score'] >= smin) &
            (df['pullback_pct'] >= pbmin) & (df['pullback_pct'] < pbmax) &
            (df['wave1_gain'] >= smin_w) & (df['wave1_gain'] < smax_w))
    sub = df[mask]
    n = len(sub)
    if n == 0:
        continue
    hit20 = sub['hit_20'].mean() * 100
    stop = sub['hit_stop'].mean() * 100
    est_daily = n / 41 * 1.03 * 4.7
    # 估算有信号天数（22个交易日，随机分布）
    total_signals = est_daily * 22
    days_with = 22 * (1 - (21/22) ** total_signals) if total_signals > 0 else 0
    print(f"  {name}")
    print(f"    样本数: {n}只 | +20%胜率: {hit20:.1f}% | 止损率: {stop:.1f}%")
    print(f"    推算日均: {est_daily:.2f}只 | 有信号天数约: {days_with:.0f}/22天 ({days_with/22*100:.0f}%)")
    print()
