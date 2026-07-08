"""波浪策略参数优化器 - 基于历史交易数据寻找最优参数组合。

分析维度：
  1. 信号分阈值 (80/85/90/95)
  2. 第1浪涨幅区间 (找最优W1高度)
  3. 第2浪回调深度 (介入时点)
  4. 组合过滤条件的最优搭配
"""
import os
import sys
import numpy as np
import pandas as pd
from itertools import product

sys.path.insert(0, r'd:\mystock\solo')
os.chdir(r'd:\mystock\solo')

trades_path = r'd:\mystock\solo\etf_resonance\output\backtest_wave3_trades.csv'
df = pd.read_csv(trades_path)

print("=" * 80)
print("          🔬 波浪策略参数优化分析")
print("=" * 80)

print(f"\n总交易数: {len(df)} | 总胜率: {(df['return_pct']>0).mean()*100:.1f}% | "
      f"平均收益: {df['return_pct'].mean():+.2f}%")

# ============== 1. 信号分阈值分析 ==============
print("\n" + "-" * 80)
print("【1】信号分阈值分析")
print("-" * 80)
for thresh in [80, 85, 90, 93, 95]:
    sub = df[df['signal_score'] >= thresh]
    if len(sub) == 0:
        continue
    wr = (sub['return_pct'] > 0).mean() * 100
    avg = sub['return_pct'].mean()
    win_avg = sub[sub['return_pct'] > 0]['return_pct'].mean() if len(sub[sub['return_pct']>0])>0 else 0
    lose_avg = sub[sub['return_pct'] <= 0]['return_pct'].mean() if len(sub[sub['return_pct']<=0])>0 else 0
    profit_factor = abs(win_avg * len(sub[sub['return_pct']>0]) / (lose_avg * len(sub[sub['return_pct']<=0]) + 1e-6))
    print(f"  ≥{thresh}: {len(sub)}笔 | 胜率{wr:.1f}% | 均收益{avg:+.2f}% | "
          f"盈{win_avg:+.1f}/亏{lose_avg:+.1f} | 盈亏比{profit_factor:.2f}")

# ============== 2. 第1浪涨幅区间分析 ==============
print("\n" + "-" * 80)
print("【2】第1浪涨幅区间分析（找最优W1高度）")
print("-" * 80)
w1_bins = [(0, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0), (1.0, 1.3), (1.3, 1.6), (1.6, 3.0)]
for lo, hi in w1_bins:
    sub = df[(df['w1_gain'] >= lo) & (df['w1_gain'] < hi)]
    if len(sub) == 0:
        continue
    wr = (sub['return_pct'] > 0).mean() * 100
    avg = sub['return_pct'].mean()
    win_avg = sub[sub['return_pct'] > 0]['return_pct'].mean() if len(sub[sub['return_pct']>0])>0 else 0
    lose_avg = sub[sub['return_pct'] <= 0]['return_pct'].mean() if len(sub[sub['return_pct']<=0])>0 else 0
    pf = abs(win_avg * len(sub[sub['return_pct']>0]) / (lose_avg * len(sub[sub['return_pct']<=0]) + 1e-6))
    print(f"  W1 [{lo*100:.0f}%-{hi*100:.0f}%): {len(sub)}笔 | 胜率{wr:.1f}% | "
          f"均收益{avg:+.2f}% | 盈亏比{pf:.2f}")

# ============== 3. 第2浪回调深度分析 ==============
print("\n" + "-" * 80)
print("【3】第2浪回调深度分析（最佳介入时点）")
print("-" * 80)
w2_bins = [(0, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.85)]
for lo, hi in w2_bins:
    sub = df[(df['w2_retrace'] >= lo) & (df['w2_retrace'] < hi)]
    if len(sub) == 0:
        continue
    wr = (sub['return_pct'] > 0).mean() * 100
    avg = sub['return_pct'].mean()
    win_avg = sub[sub['return_pct'] > 0]['return_pct'].mean() if len(sub[sub['return_pct']>0])>0 else 0
    lose_avg = sub[sub['return_pct'] <= 0]['return_pct'].mean() if len(sub[sub['return_pct']<=0])>0 else 0
    pf = abs(win_avg * len(sub[sub['return_pct']>0]) / (lose_avg * len(sub[sub['return_pct']<=0]) + 1e-6))
    print(f"  W2回调[{lo*100:.0f}%-{hi*100:.0f}%): {len(sub)}笔 | 胜率{wr:.1f}% | "
          f"均收益{avg:+.2f}% | 盈亏比{pf:.2f}")

# ============== 4. 排除止损/止盈后的调仓换股胜率 ==============
print("\n" + "-" * 80)
print("【4】排除止损止盈后的调仓换股表现")
print("-" * 80)
rotate_df = df[df['exit_reason'] == '调仓换股']
if len(rotate_df) > 0:
    for thresh in [85, 90, 93, 95]:
        sub = rotate_df[rotate_df['signal_score'] >= thresh]
        if len(sub) == 0:
            continue
        wr = (sub['return_pct'] > 0).mean() * 100
        avg = sub['return_pct'].mean()
        print(f"  ≥{thresh}调仓换股: {len(sub)}笔 | 胜率{wr:.1f}% | 均收益{avg:+.2f}%")

# ============== 5. 组合参数网格搜索 ==============
print("\n" + "-" * 80)
print("【5】组合参数网格搜索（信号分 × W1涨幅 × W2回调）")
print("-" * 80)

score_threshes = [85, 90, 93]
w1_ranges = [(0, 0.6), (0.4, 1.0), (0.5, 1.0), (0.4, 1.3), (0.6, 1.6), (0.8, 2.0)]
w2_ranges = [(0, 0.5), (0.3, 0.6), (0.4, 0.7), (0.3, 0.7), (0.5, 0.8)]

results = []
for st, (w1_lo, w1_hi), (w2_lo, w2_hi) in product(score_threshes, w1_ranges, w2_ranges):
    sub = df[
        (df['signal_score'] >= st) &
        (df['w1_gain'] >= w1_lo) & (df['w1_gain'] < w1_hi) &
        (df['w2_retrace'] >= w2_lo) & (df['w2_retrace'] < w2_hi)
    ]
    if len(sub) < 5:
        continue
    wr = (sub['return_pct'] > 0).mean() * 100
    avg = sub['return_pct'].mean()
    win_sub = sub[sub['return_pct'] > 0]
    lose_sub = sub[sub['return_pct'] <= 0]
    win_avg = win_sub['return_pct'].mean() if len(win_sub)>0 else 0
    lose_avg = lose_sub['return_pct'].mean() if len(lose_sub)>0 else 0
    pf = abs(win_avg * len(win_sub) / (lose_avg * len(lose_sub) + 1e-6))
    results.append({
        'score_ge': st,
        'w1_range': f'{w1_lo*100:.0f}-{w1_hi*100:.0f}%',
        'w2_range': f'{w2_lo*100:.0f}-{w2_hi*100:.0f}%',
        'trades': len(sub),
        'win_rate': round(wr, 1),
        'avg_ret': round(avg, 2),
        'profit_factor': round(pf, 2),
        'score': round(wr * 0.4 + avg * 0.4 + min(pf, 5) * 0.2 * 10, 1),
    })

results_df = pd.DataFrame(results)
if not results_df.empty:
    results_df = results_df.sort_values('score', ascending=False)
    print("\n  Top 15 参数组合（综合评分 = 胜率×0.4 + 均收益×0.4 + 盈亏比×0.2）:")
    print(f"  {'信号分':<8}{'W1涨幅':<12}{'W2回调':<12}{'笔数':<6}{'胜率':<8}"
          f"{'均收益':<10}{'盈亏比':<8}{'综合分':<8}")
    print(f"  {'-'*72}")
    for _, r in results_df.head(15).iterrows():
        print(f"  ≥{r['score_ge']:<7}{r['w1_range']:<12}{r['w2_range']:<12}"
              f"{r['trades']:<6}{r['win_rate']:.1f}%{'':>3}{r['avg_ret']:+.2f}%{'':>3}"
              f"{r['profit_factor']:.2f}{'':>3}{r['score']:.1f}")

# ============== 6. 最佳介入时点分析 ==============
print("\n" + "-" * 80)
print("【6】最佳介入时点分析（排除止损止盈干扰，看调仓换股的持仓表现）")
print("-" * 80)
print("\n  分析思路：止盈=+20%上限截断，止损=-8%下限截断，调仓换股=真实市场表现")

best_combo = results_df.iloc[0] if not results_df.empty else None
if best_combo is not None:
    print(f"\n  🏆 最优参数组合:")
    print(f"    信号分 ≥ {best_combo['score_ge']}")
    print(f"    W1涨幅: {best_combo['w1_range']}")
    print(f"    W2回调: {best_combo['w2_range']}")
    print(f"    交易数: {best_combo['trades']}笔")
    print(f"    胜率: {best_combo['win_rate']}%")
    print(f"    均收益: {best_combo['avg_ret']:+.2f}%")
    print(f"    盈亏比: {best_combo['profit_factor']}")

# ============== 7. 排除大盘空仓的干扰 ==============
print("\n" + "-" * 80)
print("【7】排除大盘空仓干扰（只看止损/止盈/调仓换股的正常交易）")
print("-" * 80)
normal_df = df[df['exit_reason'] != '大盘空仓']
if len(normal_df) > 0:
    print(f"\n  正常交易: {len(normal_df)}笔 | 胜率{(normal_df['return_pct']>0).mean()*100:.1f}% | "
          f"均收益{normal_df['return_pct'].mean():+.2f}%")
    for st in [85, 90, 93]:
        sub = normal_df[normal_df['signal_score'] >= st]
        if len(sub) == 0:
            continue
        wr = (sub['return_pct'] > 0).mean() * 100
        avg = sub['return_pct'].mean()
        print(f"    ≥{st}: {len(sub)}笔 | 胜率{wr:.1f}% | 均收益{avg:+.2f}%")

print("\n" + "=" * 80)
print("  📋 优化结论")
print("=" * 80)
