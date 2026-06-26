# -*- coding: utf-8 -*-
"""分析创新高回测结果 - 纯英文输出"""
import pandas as pd
import numpy as np

csv = r'D:\mystock\solo\multi_factor_picker\output\new_high_pullback_backtest_20260626_124930.csv'
df = pd.read_csv(csv)
df['is_new_high'] = df['is_new_high'].astype(bool)
df['above_ma60'] = df['above_ma60'].astype(bool)
df['above_ma120'] = df['above_ma120'].astype(bool)
df['above_ma250'] = df['above_ma250'].astype(bool)
df['vol_shrink'] = df['vol_shrink'].astype(bool)
df['new_high_60'] = df['new_high_60'].astype(bool)
df['new_high_120'] = df['new_high_120'].astype(bool)
df['new_high_250'] = df['new_high_250'].astype(bool)
df['three_ma_support'] = df['three_ma_support'].astype(bool)
df['success'] = df['success'].astype(bool)

print(f"Total samples: {len(df):,}")
print(f"Unique stocks: {df['code'].nunique()}")
print(f"Date range: {df['date'].min()} ~ {df['date'].max()}")
print()

print("=" * 70)
print("CORE: New-High Peak vs Non-New-High Peak - 2nd Wave Success")
print("=" * 70)

for hold in [5, 10, 20, 30]:
    sub = df[df['hold_days'] == hold]
    print(f"\n-- Hold {hold}d --")
    for nh in [True, False]:
        s = sub[sub['is_new_high'] == nh]
        if len(s) < 50:
            print(f"  {'NH' if nh else 'NONH'}: {len(s)} samples (insufficient)")
            continue
        wr = s['success'].mean() * 100
        avg = s['gain'].mean()
        med = s['gain'].median()
        wr5 = (s['gain'] > 5).mean() * 100
        wr10 = (s['gain'] > 10).mean() * 100
        max_avg = s['max_gain'].mean()
        min_avg = s['min_gain'].mean()
        print(f"  {'NH' if nh else 'NONH'} ({len(s):,}): WR={wr:.1f}% Avg={avg:+.2f}% Med={med:+.2f}% >5%={wr5:.1f}% >10%={wr10:.1f}% MaxG={max_avg:+.2f}% MinG={min_avg:+.2f}%")

print(f"\n{'='*70}")

# New high window breakdown
print(f"\n--- By New-High Window (Hold 10d) ---")
for win, label in [('new_high_60', '60d'), ('new_high_120', '120d'), ('new_high_250', '250d')]:
    print(f"  {label}:")
    h10 = df[df['hold_days'] == 10]
    for nh in [True, False]:
        s = h10[h10[win] == nh]
        if len(s) < 50:
            continue
        wr = s['success'].mean() * 100
        avg = s['gain'].mean()
        print(f"    {'NH' if nh else 'NONH'}: {len(s):,} samp WR={wr:.1f}% Avg={avg:+.2f}%")

print(f"\n{'='*70}")

# Best combo search
print(f"\n--- Best Condition Combos (Hold 10d, >=100 samples) ---")
h10 = df[df['hold_days'] == 10]
grp = h10.groupby(['is_new_high', 'above_ma60', 'above_ma120', 'above_ma250', 'vol_shrink'])
summary = grp.agg(
    count=('success', 'count'),
    win_rate=('success', 'mean'),
    avg_gain=('gain', 'mean'),
    med_gain=('gain', 'median'),
).reset_index()
summary = summary[summary['count'] >= 100].sort_values('win_rate', ascending=False)
for _, r in summary.head(10).iterrows():
    print(f"  NH={int(r['is_new_high'])} MA60={int(r['above_ma60'])} MA120={int(r['above_ma120'])} MA250={int(r['above_ma250'])} Shrink={int(r['vol_shrink'])}: {int(r['count']):,}samp WR={r['win_rate']*100:.1f}% Avg={r['avg_gain']*100:+.2f}%")

print(f"\n{'='*70}")

# Cross analysis: new_high_250 + 3MA
print(f"\n--- Cross: NH250 + 3MA Support (Hold 10d) ---")
h10 = df[df['hold_days'] == 10]
for nh in [True, False]:
    for ma in [True, False]:
        s = h10[(h10['new_high_250'] == nh) & (h10['three_ma_support'] == ma)]
        if len(s) < 50:
            continue
        wr = s['success'].mean() * 100
        avg = s['gain'].mean()
        print(f"  NH250={'Y' if nh else 'N'} 3MA={'Y' if ma else 'N'}: {len(s):,}samp WR={wr:.1f}% Avg={avg:+.2f}%")

print(f"\n{'='*70}")

print("\n--- FAILURE ANALYSIS (Hold 10d, Top 10 biggest losers for NH) ---")
fail = h10[h10['is_new_high'] == True].nsmallest(10, 'gain')
for _, r in fail.iterrows():
    print(f"  {r['code']} {r['date']} W1={r['wave1_gain']:.0f}% PB={r['pullback_pct']:.0f}% MA60={int(r['above_ma60'])} MA120={int(r['above_ma120'])} MA250={int(r['above_ma250'])} | Gain={r['gain']:+.2f}%")

print(f"\n{'='*70}")
print("SUMMARY TABLE (all hold periods)")
print(f"{'Period':>10} | {'NH WR':>8} | {'NONH WR':>10} | {'Diff':>8} | {'NH Avg':>10} | {'NONH Avg':>10}")
print("-" * 60)
for hold in [5, 10, 20, 30]:
    h = df[df['hold_days'] == hold]
    nh_s = h[h['is_new_high']==True]
    no_s = h[h['is_new_high']==False]
    nh_wr = nh_s['success'].mean() * 100
    no_wr = no_s['success'].mean() * 100
    nh_avg = nh_s['gain'].mean()
    no_avg = no_s['gain'].mean()
    print(f"{f'Hold{hold}d':>10} | {nh_wr:>7.1f}% | {no_wr:>9.1f}% | {nh_wr-no_wr:>+7.1f}% | {nh_avg:>+9.2f}% | {no_avg:>+9.2f}%")

print("\nDone.")
