# -*- coding: utf-8 -*-
"""分析强势横盘的最佳调整天数"""
import pandas as pd
import numpy as np

csv = r'D:\mystock\solo\multi_factor_picker\output\new_high_pullback_backtest_20260626_124930.csv'
df = pd.read_csv(csv)

# 筛选强势横盘条件（调整≤15天，回调<10%，缩量）
cond = (
    (df['pullback_pct'] < 10) &  # 回调<10%
    (df['hold_days'] == 10)      # 固定持有10天
)
df_sw = df[cond].copy()
df_sw['adjust_days'] = df_sw.apply(
    lambda r: 0, axis=1  # 数据里没有adjust_days字段
)
print(f"满足强势横盘条件的样本: {len(df_sw)}")

# 换个思路 - 用pullback < 10% + 按hold_days分组的胜率
print("\n=== 所有回调<10%的样本（=强势横盘候选）按调整天数估算 ===")

# 我们没有直接的adjust_days字段，但可以从pullback和vol_shrink推断横盘特征
# 看看不同持有天数下的胜率分布
for hold in [5, 10, 20, 30]:
    sub = df[(df['pullback_pct'] < 10) & (df['hold_days'] == hold)]
    if len(sub) < 100:
        continue
    # 按pullback分段
    for pb_min, pb_max, label in [(0, 3, 'PB<3%'), (3, 6, 'PB3-6%'), (6, 10, 'PB6-10%')]:
        s = sub[(sub['pullback_pct'] >= pb_min) & (sub['pullback_pct'] < pb_max)]
        if len(s) < 20: continue
        wr = s['success'].mean() * 100
        avg = s['gain'].mean()
        print(f"  Hold{hold}d {label}: {len(s):>5,}笔 WR={wr:5.1f}% Avg={avg:+6.2f}%")
    # 缩量vs非缩量
    for shrink, slabel in [(True, '缩量'), (False, '非缩量')]:
        s = sub[sub['vol_shrink'] == shrink]
        if len(s) < 50: continue
        wr = s['success'].mean() * 100
        avg = s['gain'].mean()
        print(f"  Hold{hold}d {slabel}: {len(s):>5,}笔 WR={wr:5.1f}% Avg={avg:+6.2f}%")
    print()
