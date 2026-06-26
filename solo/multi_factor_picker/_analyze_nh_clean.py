# -*- coding: utf-8 -*-
"""输出清理版本"""
import pandas as pd
import numpy as np

csv = r'D:\mystock\solo\multi_factor_picker\output\new_high_pullback_backtest_20260626_124930.csv'
df = pd.read_csv(csv)

# 统计数据量分布
print(f"总样本: {len(df):,}")
print(f"覆盖股票: {df['code'].nunique()}")

h10 = df[df['hold_days'] == 10]
print(f"\n持有10天样本分布:")
print(f"  创新高(any): {h10['is_new_high'].sum():,} vs 未创新高: {(~h10['is_new_high'].astype(bool)).sum():,}")
print(f"  创新高250日: {h10['new_high_250'].sum():,} vs 未创新高250日: {(~h10['new_high_250'].astype(bool)).sum():,}")
print(f"  三均线支撑: {h10['three_ma_support'].sum():,} vs 无支撑: {(~h10['three_ma_support'].astype(bool)).sum():,}")
print(f"  缩量: {h10['vol_shrink'].sum():,} vs 非缩量: {(~h10['vol_shrink'].astype(bool)).sum():,}")
print(f"  三均线+创新高250: {((h10['new_high_250']==True)&(h10['three_ma_support']==True)).sum():,}")
print(f"  三均线+未创新高250: {((h10['new_high_250']==False)&(h10['three_ma_support']==True)).sum():,}")

# 创新高250日 + 有/无三均线
print(f"\n--- 250日创新高 vs 三均线支撑 交叉 ---")
for nh in [True, False]:
    for ma in [True, False]:
        s = df[(df['hold_days']==10)&(df['new_high_250']==nh)&(df['three_ma_support']==ma)]
        if len(s) < 50: continue
        wr = s['success'].mean()*100
        avg = s['gain'].mean()
        wr5 = (s['gain']>5).mean()*100
        wr10 = (s['gain']>10).mean()*100
        print(f"  250NH={int(nh)} 3MA={int(ma)}: {len(s):>6,}笔 | WR={wr:5.1f}% | Avg={avg:+6.2f}% | >5%={wr5:5.1f}% | >10%={wr10:5.1f}%")

# 不同回落幅度
print(f"\n--- 创新高后不同回落幅度 (250日NH, 持有10天) ---")
nh250 = df[(df['hold_days']==10)&(df['new_high_250']==True)&(df['is_new_high']==True)]
for pb_label, lo, hi in [('PB<10%', 0, 10), ('PB10-15%', 10, 15), ('PB15-20%', 15, 20), ('PB20-30%', 20, 30), ('PB30%+', 30, 100)]:
    s = nh250[(nh250['pullback_pct']>=lo)&(nh250['pullback_pct']<hi)]
    if len(s) < 20: continue
    wr = s['success'].mean()*100
    avg = s['gain'].mean()
    print(f"  {pb_label}: {len(s):>5,}笔 | WR={wr:5.1f}% | Avg={avg:+6.2f}%")

print(f"\n--- 最后一列: 三均线组合去掉后 优势组合 ---")
no_3ma = df[(df['hold_days']==10)&(df['three_ma_support']==False)]
for nh in [False, True]:
    s = no_3ma[no_3ma['is_new_high']==nh]
    if len(s) < 50: continue
    wr = s['success'].mean()*100
    avg = s['gain'].mean()
    print(f"  NH={int(nh)} (无三均线): {len(s):>6,}笔 | WR={wr:5.1f}% | Avg={avg:+6.2f}%")

# 再放一下hold 5 20 30 无三均线的
for hold in [5, 20, 30]:
    s = df[(df['hold_days']==hold)&(df['three_ma_support']==False)]
    for nh in [False, True]:
        ss = s[s['is_new_high']==nh]
        if len(ss) < 50: continue
        wr = ss['success'].mean()*100
        avg = ss['gain'].mean()
        print(f"  Hold{hold}d NH={int(nh)} (无三均线): {len(ss):>6,}笔 | WR={wr:5.1f}% | Avg={avg:+6.2f}%")
