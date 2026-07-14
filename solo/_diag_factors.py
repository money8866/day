import pandas as pd
import numpy as np

df = pd.read_csv('theme_forecast/output/adaptive_backtest_reclassified.csv')

print("=" * 70)
print("抱团下跌期各因子方向诊断（826样本）")
print("=" * 70)

sub = df[df['regime_new'] == '抱团下跌']
print(f"样本: {len(sub)}, 实际平均收益: {sub['actual_ret'].mean():+.2f}%, 上涨率: {sub['actual_up'].mean()*100:.1f}%")
print()

# 各因子与实际收益的相关性
factors = ['f_rs', 'f_mom', 'f_adx', 'f_syn', 'f_div', 'f_brk',
           'f_rs_slope', 'f_concentration', 'f_leader_lag']
print(f"{'因子':<15} {'IC':>8} {'Q1收益':>8} {'Q5收益':>8} {'方向':>8}")
print("-" * 50)
for f in factors:
    if f not in sub.columns:
        continue
    valid = sub[[f, 'actual_ret']].dropna()
    if len(valid) < 50:
        continue
    ic = valid[f].corr(valid['actual_ret'], method='spearman')
    try:
        valid['g'] = pd.qcut(valid[f], q=5, duplicates='drop')
        g = valid.groupby('g', observed=True)['actual_ret'].mean()
        q1 = g.iloc[0] if len(g) >= 2 else 0
        q5 = g.iloc[-1] if len(g) >= 2 else 0
    except Exception:
        q1 = q5 = 0
    direction = "正向" if q5 > q1 else "反向"
    print(f"{f:<15} {ic:>8.4f} {q1:>+7.2f}% {q5:>+7.2f}% {direction:>8}")

print()
print("=" * 70)
print("抱团震荡期各因子方向诊断（1239样本）")
print("=" * 70)
sub2 = df[df['regime_new'] == '抱团震荡']
print(f"样本: {len(sub2)}, 实际平均收益: {sub2['actual_ret'].mean():+.2f}%, 上涨率: {sub2['actual_up'].mean()*100:.1f}%")
print()
print(f"{'因子':<15} {'IC':>8} {'Q1收益':>8} {'Q5收益':>8} {'方向':>8}")
print("-" * 50)
for f in factors:
    if f not in sub2.columns:
        continue
    valid = sub2[[f, 'actual_ret']].dropna()
    if len(valid) < 50:
        continue
    ic = valid[f].corr(valid['actual_ret'], method='spearman')
    try:
        valid['g'] = pd.qcut(valid[f], q=5, duplicates='drop')
        g = valid.groupby('g', observed=True)['actual_ret'].mean()
        q1 = g.iloc[0] if len(g) >= 2 else 0
        q5 = g.iloc[-1] if len(g) >= 2 else 0
    except Exception:
        q1 = q5 = 0
    direction = "正向" if q5 > q1 else "反向"
    print(f"{f:<15} {ic:>8.4f} {q1:>+7.2f}% {q5:>+7.2f}% {direction:>8}")

print()
print("=" * 70)
print("轮动期各因子方向诊断（19352样本）")
print("=" * 70)
sub3 = df[df['regime_new'] == '轮动']
print(f"样本: {len(sub3)}, 实际平均收益: {sub3['actual_ret'].mean():+.2f}%, 上涨率: {sub3['actual_up'].mean()*100:.1f}%")
print()
print(f"{'因子':<15} {'IC':>8} {'Q1收益':>8} {'Q5收益':>8} {'方向':>8}")
print("-" * 50)
for f in factors:
    if f not in sub3.columns:
        continue
    valid = sub3[[f, 'actual_ret']].dropna()
    if len(valid) < 50:
        continue
    ic = valid[f].corr(valid['actual_ret'], method='spearman')
    try:
        valid['g'] = pd.qcut(valid[f], q=5, duplicates='drop')
        g = valid.groupby('g', observed=True)['actual_ret'].mean()
        q1 = g.iloc[0] if len(g) >= 2 else 0
        q5 = g.iloc[-1] if len(g) >= 2 else 0
    except Exception:
        q1 = q5 = 0
    direction = "正向" if q5 > q1 else "反向"
    print(f"{f:<15} {ic:>8.4f} {q1:>+7.2f}% {q5:>+7.2f}% {direction:>8}")
