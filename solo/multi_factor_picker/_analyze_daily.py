# -*- coding: utf-8 -*-
"""
分析：不同宽松程度下的信号质量和数量
目标：确保每天至少一只
"""
import pandas as pd
import numpy as np

df = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\sideways_analysis.csv')
print(f"总样本: {len(df)}只（沪深300池）")
print()

# 基于实际验证：400只合格股，6月8个信号（评分≥25+回调3-10%+一波20-60%）
# 推算全量946只: 约19个/月
# 基准放大系数: 从18个样本(sideways) -> 19个/月(实际)
base_monthly = 19  # 全量946只，评分≥25+回调3-10%+一波20-60%
base_sample_count = 18

def estimate_monthly(sample_count):
    """根据样本数估算全量946只的月信号数"""
    return sample_count / base_sample_count * base_monthly

def estimate_days_with_signal(monthly_count, total_days=22):
    """估算有信号天数（随机分布假设）"""
    if monthly_count <= 0:
        return 0
    return total_days * (1 - ((total_days - 1) / total_days) ** monthly_count)

print("=" * 90)
print(f"{'方案':<35} {'样本数':>5} {'月信号':>6} {'有信号天':>7} {'占比':>6} {'+20%胜':>7} {'止损率':>6}")
print("-" * 90)

# 方案1: 当前v3.3
mask1 = (df['score'] >= 25) & (df['pullback_pct'] >= 3) & (df['pullback_pct'] < 10) & (df['wave1_gain'] >= 20) & (df['wave1_gain'] < 60)
s1 = df[mask1]
m1 = estimate_monthly(len(s1))
d1 = estimate_days_with_signal(m1)
print(f"v3.3: 评分≥25+回调3-10%+一波20-60%  {len(s1):>5} {m1:>6.0f} {d1:>6.0f}/22 {d1/22*100:>5.0f}% {s1['hit_20'].mean()*100:>6.1f}% {s1['hit_stop'].mean()*100:>5.1f}%")

# 方案2: 评分降到24
for smin in [24, 23, 22, 20]:
    mask = (df['score'] >= smin) & (df['pullback_pct'] >= 3) & (df['pullback_pct'] < 10) & (df['wave1_gain'] >= 20) & (df['wave1_gain'] < 60)
    s = df[mask]
    if len(s) == 0:
        continue
    m = estimate_monthly(len(s))
    d = estimate_days_with_signal(m)
    print(f"评分≥{smin}+回调3-10%+一波20-60%{'':<10} {len(s):>5} {m:>6.0f} {d:>6.0f}/22 {d/22*100:>5.0f}% {s['hit_20'].mean()*100:>6.1f}% {s['hit_stop'].mean()*100:>5.1f}%")

print()

# 方案3: 扩大回调范围
for pb_min in [2, 1, 0]:
    mask = (df['score'] >= 25) & (df['pullback_pct'] >= pb_min) & (df['pullback_pct'] < 10) & (df['wave1_gain'] >= 20) & (df['wave1_gain'] < 60)
    s = df[mask]
    if len(s) == 0:
        continue
    m = estimate_monthly(len(s))
    d = estimate_days_with_signal(m)
    print(f"评分≥25+回调{pb_min}-10%+一波20-60%{'':<11} {len(s):>5} {m:>6.0f} {d:>6.0f}/22 {d/22*100:>5.0f}% {s['hit_20'].mean()*100:>6.1f}% {s['hit_stop'].mean()*100:>5.1f}%")

print()

# 方案4: 扩大一波范围
mask = (df['score'] >= 25) & (df['pullback_pct'] >= 3) & (df['pullback_pct'] < 10) & (df['wave1_gain'] >= 15) & (df['wave1_gain'] < 80)
s = df[mask]
m = estimate_monthly(len(s))
d = estimate_days_with_signal(m)
print(f"评分≥25+回调3-10%+一波15-80%{'':<11} {len(s):>5} {m:>6.0f} {d:>6.0f}/22 {d/22*100:>5.0f}% {s['hit_20'].mean()*100:>6.1f}% {s['hit_stop'].mean()*100:>5.1f}%")

print()

# 方案5: 组合放宽
combos = [
    ("评分≥22+回调2-10%+一波20-60%", 22, 2, 10, 20, 60),
    ("评分≥22+回调2-12%+一波20-70%", 22, 2, 12, 20, 70),
    ("评分≥20+回调2-10%+一波20-60%", 20, 2, 10, 20, 60),
    ("评分≥20+回调0-12%+一波15-80%", 20, 0, 12, 15, 80),
    ("评分≥22+回调0-15%+一波15-100%", 22, 0, 15, 15, 100),
]

print("组合放宽方案:")
for name, smin, pbmin, pbmax, smin_w, smax_w in combos:
    mask = ((df['score'] >= smin) &
            (df['pullback_pct'] >= pbmin) & (df['pullback_pct'] < pbmax) &
            (df['wave1_gain'] >= smin_w) & (df['wave1_gain'] < smax_w))
    s = df[mask]
    if len(s) == 0:
        continue
    m = estimate_monthly(len(s))
    d = estimate_days_with_signal(m)
    print(f"  {name:<30} {len(s):>5} {m:>6.0f} {d:>6.0f}/22 {d/22*100:>5.0f}% {s['hit_20'].mean()*100:>6.1f}% {s['hit_stop'].mean()*100:>5.1f}%")

print()
print("=" * 90)
print("结论：")
print("  要达到90%+天数有信号（每天至少1只），需要月信号约50个")
print("  单靠强势横盘一种形态，即使大幅放宽，也只能达到约40个/月（80%天数）")
print("  建议：结合多种形态（强势横盘+V型+放量回调），或者放宽到评分≥20+较宽范围")
