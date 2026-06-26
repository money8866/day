# -*- coding: utf-8 -*-
"""分析创新高回测结果"""
import pandas as pd
import numpy as np

csv = r'D:\mystock\solo\multi_factor_picker\output\new_high_pullback_backtest_20260626_124930.csv'
df = pd.read_csv(csv)
print(f"总样本: {len(df):,} 笔")
print(f"覆盖股票: {df['code'].nunique()} 只")
print(f"时间跨度: {df['date'].min()} ~ {df['date'].max()}")
print()

# ═══════════════════════════════════════════
# 核心问题：创新高 vs 未创新高
# ═══════════════════════════════════════════
print("=" * 80)
print("【核心结论】波峰是否创新高的二波胜率对比")
print("=" * 80)

for hold in [5, 10, 20, 30]:
    sub = df[df['hold_days'] == hold]
    print(f"\n── 持有{hold}天 ──")
    for nh in [True, False]:
        s = sub[sub['is_new_high'] == nh]
        if len(s) < 50:
            print(f"  {'创' if nh else '未创'}新高: {len(s)}笔（样本不足）")
            continue
        wr = s['success'].mean() * 100
        avg = s['gain'].mean()
        med = s['gain'].median()
        max_g = s['max_gain'].mean()
        min_g = s['min_gain'].mean()
        wr_5pct = (s['gain'] > 5).mean() * 100
        wr_10pct = (s['gain'] > 10).mean() * 100
        print(f"  {'✅ 创新高' if nh else '❌ 未创新高'} ({len(s):,}笔):")
        print(f"     胜率{wr:.1f}% | 均收益{avg:+.2f}% | 中位{med:+.2f}% | >5%: {wr_5pct:.1f}% | >10%: {wr_10pct:.1f}%")
        print(f"     最大均收益{max_g:+.2f}% | 最差均收益{min_g:+.2f}%")

# ═══════════════════════════════════════════
# 按新高窗口分层
# ═══════════════════════════════════════════
print(f"\n{'='*80}")
print("【按N日新高窗口分层 + 持有10天】")
print("=" * 80)

for win in ['new_high_60', 'new_high_120', 'new_high_250']:
    label = win.replace('_', ' ').upper()
    print(f"\n── {label} ──")
    sub = df[(df['hold_days'] == 10)]
    for nh in [True, False]:
        s = sub[sub[win] == nh]
        if len(s) < 50:
            print(f"  {'创' if nh else '非'}{win.split('_')[-1]}日新高: {len(s)}笔（样本不足）")
            continue
        wr = s['success'].mean() * 100
        avg = s['gain'].mean()
        print(f"  {'✅ 创' if nh else '❌ 非'}新高: {len(s):,}笔 | 胜率{wr:.1f}% | 均收益{avg:+.2f}%")

# ═══════════════════════════════════════════
# 组合最优
# ═══════════════════════════════════════════
print(f"\n{'='*80}")
print("【最优条件组合 TOP15 (持有10天, 样本≥100)】")
print("=" * 80)

h10 = df[df['hold_days'] == 10].copy()
grp = h10.groupby(['is_new_high', 'above_ma60', 'above_ma120', 'above_ma250', 'vol_shrink'])
summary = grp.agg(
    count=('success', 'count'),
    win_rate=('success', 'mean'),
    avg_gain=('gain', 'mean'),
    med_gain=('gain', 'median'),
    avg_max=('max_gain', 'mean'),
).reset_index()
summary = summary[summary['count'] >= 100].sort_values('win_rate', ascending=False)

for _, r in summary.head(15).iterrows():
    print(f"  #{int(_)+1} | 新高={int(r['is_new_high'])} MA60={int(r['above_ma60'])} MA120={int(r['above_ma120'])} MA250={int(r['above_ma250'])} 缩量={int(r['vol_shrink'])}")
    print(f"    样本{int(r['count']):,}笔 | 胜率{r['win_rate']*100:.1f}% | 均收益{r['avg_gain']*100:+.2f}% | 中位{r['med_gain']*100:+.2f}%")

# ═══════════════════════════════════════════
# 创新高+三均线 vs 未创新高+三均线
# ═══════════════════════════════════════════
print(f"\n{'='*80}")
print("【创新高 + 三均线支撑 交叉分析 (持有10天)】")
print("=" * 80)

h10 = df[df['hold_days'] == 10]
for nh in [True, False]:
    for ma in [True, False]:
        s = h10[(h10['is_new_high'] == nh) & (h10['three_ma_support'] == ma)]
        if len(s) < 50:
            continue
        wr = s['success'].mean() * 100
        avg = s['gain'].mean()
        print(f"  {'创' if nh else '未创'}新高+{'✅三均线' if ma else '❌无均线'}: {len(s):,}笔 | 胜率{wr:.1f}% | 均收益{avg:+.2f}%")

# ═══════════════════════════════════════════
# 最差情况分析
# ═══════════════════════════════════════════
print(f"\n{'='*80}")
print("【创新高失败案例分析 (持有10天, 前10笔最大亏损)】")
print("=" * 80)
fail = h10[(h10['is_new_high'] == True)].nsmallest(10, 'gain')
for _, r in fail.iterrows():
    print(f"  {r['code']} {r['date']} 一波+{r['wave1_gain']:.0f}% 回落{r['pullback_pct']:.0f}% MA60={int(r['above_ma60'])} MA120={int(r['above_ma120'])} MA250={int(r['above_ma250'])} | 收益{r['gain']:+.2f}%")

print(f"\n{'='*80}")
print("【各持有天数总结表】")
print("=" * 80)
print(f"{'持有天数':>8} | {'创新高胜率':>12} | {'未创新高胜率':>14} | {'胜率差距':>10} | {'创新高收益':>14} | {'未创新高收益':>14}")
print("-" * 80)
for hold in [5, 10, 20, 30]:
    h = df[df['hold_days'] == hold]
    nh_wr = h[h['is_new_high']==True]['success'].mean() * 100 if len(h[h['is_new_high']==True]) >= 50 else 0
    no_wr = h[h['is_new_high']==False]['success'].mean() * 100 if len(h[h['is_new_high']==False]) >= 50 else 0
    nh_avg = h[h['is_new_high']==True]['gain'].mean() if len(h[h['is_new_high']==True]) >= 50 else 0
    no_avg = h[h['is_new_high']==False]['gain'].mean() if len(h[h['is_new_high']==False]) >= 50 else 0
    diff = nh_wr - no_wr
    print(f"{f'持有{hold}d':>8} | {nh_wr:>11.1f}% | {no_wr:>13.1f}% | {diff:>+9.1f}% | {nh_avg:>+13.2f}% | {no_avg:>+13.2f}%")

print("\n✅ 分析完成")
