# -*- coding: utf-8 -*-
"""
深入分析sideways_analysis.csv
找出最优条件组合
"""
import pandas as pd
import numpy as np

df = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\sideways_analysis.csv')
print(f"总样本: {len(df)}只")
print()

# 方案定义：更细粒度
plans = []

# 评分维度测试
for score_min in [30, 28, 26, 25, 24]:
    plans.append((f"评分≥{score_min} | 回调3-10% | 一波20-60%",
                  {'score_min': score_min, 'pb_min': 3, 'pb_max': 10, 's_min': 20, 's_max': 60}))

# 回调范围测试
for pb_min, pb_max in [(2, 10), (3, 10), (3, 12), (2, 12), (1, 10)]:
    plans.append((f"评分≥28 | 回调{pb_min}-{pb_max}% | 一波20-60%",
                  {'score_min': 28, 'pb_min': pb_min, 'pb_max': pb_max, 's_min': 20, 's_max': 60}))

# 一波涨幅范围测试
for s_min, s_max in [(20, 50), (20, 60), (20, 80), (25, 60), (15, 80)]:
    plans.append((f"评分≥28 | 回调3-10% | 一波{s_min}-{s_max}%",
                  {'score_min': 28, 'pb_min': 3, 'pb_max': 10, 's_min': s_min, 's_max': s_max}))

print("=" * 70)
print(f"{'方案':<40} {'数量':>4} {'+20%':>6} {'+30%':>6} {'止损率':>6} {'正收益':>6} {'均涨':>6}")
print("=" * 70)

results = []
for name, p in plans:
    mask = ((df['score'] >= p['score_min']) &
            (df['pullback_pct'] >= p['pb_min']) & (df['pullback_pct'] < p['pb_max']) &
            (df['wave1_gain'] >= p['s_min']) & (df['wave1_gain'] < p['s_max']))
    sub = df[mask]
    n = len(sub)
    if n == 0:
        continue
    
    hit20 = sub['hit_20'].mean() * 100
    hit30 = sub['hit_30'].mean() * 100
    stop_loss = sub['hit_stop'].mean() * 100
    win_rate = (sub['final_gain'] > 0).mean() * 100
    avg_max = sub['max_gain'].mean()
    
    results.append({
        'name': name,
        'n': n,
        'hit20': hit20,
        'hit30': hit30,
        'stop_loss': stop_loss,
        'win_rate': win_rate,
        'avg_max': avg_max
    })
    
    short_name = name[:38]
    print(f"{short_name:<40} {n:>4} {hit20:>5.1f}% {hit30:>5.1f}% {stop_loss:>5.1f}% {win_rate:>5.1f}% {avg_max:>5.1f}%")

print()
print("=" * 70)
print("推荐方案分析（按质量排序）：")
print()

# 综合评分：质量分 = hit20 * 0.4 + (100-stop_loss) * 0.3 + hit30 * 0.3
for r in sorted(results, key=lambda x: x['hit20'] * 0.4 + (100 - x['stop_loss']) * 0.3 + x['hit30'] * 0.3, reverse=True)[:8]:
    quality = r['hit20'] * 0.4 + (100 - r['stop_loss']) * 0.3 + r['hit30'] * 0.3
    print(f"  综合质量分: {quality:.1f} | {r['name']}")
    print(f"    数量:{r['n']}只 +20%:{r['hit20']:.1f}% +30%:{r['hit30']:.1f}% 止损:{r['stop_loss']:.1f}% 均涨:{r['avg_max']:.1f}%")
    print()

# 估算每日信号数（假设41只分布在约40个交易日，线性估算）
print("=" * 70)
print("每日信号估算（假设41只/40天=日均1.03只为基准）：")
base_daily = 41 / 40  # 基准日均
for r in sorted(results, key=lambda x: x['n'], reverse=True):
    est_daily = r['n'] / 41 * base_daily
    print(f"  {r['name'][:30]:<30} -> 日均约{est_daily:.2f}只 (总{r['n']}只)")
