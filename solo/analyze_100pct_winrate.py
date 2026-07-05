# -*- coding: utf-8 -*-
"""分析回测CSV，找出每个形态100%胜率的实盘可用过滤条件"""
import pandas as pd
import numpy as np
from itertools import product

csv_path = r'D:\mystock\solo\trend_feature_output\wave2_gem_backtest_20260703.csv'
df = pd.read_csv(csv_path)
print(f'总信号: {len(df)}')
print(f'形态分布: {df["pattern"].value_counts().to_dict()}')
print()

# 实盘可用条件维度
CONDITIONS = {
    'pullback_pct': [('>=', 15), ('>=', 18), ('>=', 20), ('>=', 22), ('>=', 25), ('>=', 28)],
    'score':        [('>=', 20), ('>=', 25), ('>=', 30), ('>=', 35)],
    'wave1_gain':   [('>=', 20), ('>=', 30), ('>=', 40), ('>=', 50)],
    'rsi':          [('<=', 30), ('<=', 40), ('<=', 50)],
    'adjust_days':  [('>=', 5), ('>=', 7), ('>=', 10)],
    'vol_ratio':    [('<=', 0.5), ('<=', 0.8), ('<=', 1.0), ('<=', 1.5)],
}

def filter_df(df, conditions):
    """conditions: dict of col -> (op, val)"""
    sub = df.copy()
    for col, (op, val) in conditions.items():
        if op == '>=':
            sub = sub[sub[col] >= val]
        elif op == '<=':
            sub = sub[sub[col] <= val]
        elif op == '>':
            sub = sub[sub[col] > val]
        elif op == '<':
            sub = sub[sub[col] < val]
    return sub

def stats(sub):
    if len(sub) == 0:
        return None
    w20 = sub['win_20d'].mean() * 100 if sub['win_20d'].notna().sum() > 0 else 0
    sl = sub['stop_hit'].mean() * 100
    tp = sub['target_hit'].mean() * 100
    g20 = sub['gain_20d'].mean()
    return len(sub), w20, sl, tp, g20

# 对每个形态找100%胜率条件
for pat in df['pattern'].unique():
    df_pat = df[df['pattern'] == pat]
    print(f'\n{"="*80}')
    print(f'  [{pat}] 总{len(df_pat)}个信号')
    print(f'{"="*80}')

    # 基础胜率
    s = stats(df_pat)
    print(f'  基础: n={s[0]}, 20日胜率={s[1]:.1f}%, 止损率={s[2]:.1f}%, 止盈率={s[3]:.1f}%, 20日均涨={s[4]:.2f}%')

    # 单条件100%胜率
    print(f'\n  --- 单条件100%胜率 ---')
    found_single = []
    for col, ops in CONDITIONS.items():
        for op, val in ops:
            conds = {col: (op, val)}
            sub = filter_df(df_pat, conds)
            s = stats(sub)
            if s and s[1] == 100 and s[0] >= 3:
                found_single.append((col, op, val, s))
                print(f'    {col}{op}{val}: n={s[0]}, 20日胜率=100%, 止损率={s[2]:.1f}%, 20日均涨={s[4]:.2f}%')

    # 双条件组合100%胜率
    print(f'\n  --- 双条件组合100%胜率（信号最多）---')
    found_double = []
    cols = list(CONDITIONS.keys())
    for i in range(len(cols)):
        for j in range(i+1, len(cols)):
            c1, c2 = cols[i], cols[j]
            for op1, v1 in CONDITIONS[c1]:
                for op2, v2 in CONDITIONS[c2]:
                    conds = {c1: (op1, v1), c2: (op2, v2)}
                    sub = filter_df(df_pat, conds)
                    s = stats(sub)
                    if s and s[1] == 100 and s[0] >= 3:
                        found_double.append((c1, op1, v1, c2, op2, v2, s))

    # 按信号数排序，显示前10
    found_double.sort(key=lambda x: -x[6][0])
    for item in found_double[:10]:
        c1, op1, v1, c2, op2, v2, s = item
        print(f'    {c1}{op1}{v1} & {c2}{op2}{v2}: n={s[0]}, 20日胜率=100%, 止损率={s[2]:.1f}%, 20日均涨={s[4]:.2f}%')

    # 找信号最多的100%胜率组合
    if found_double:
        best = max(found_double, key=lambda x: x[6][0])
        c1, op1, v1, c2, op2, v2, s = best
        print(f'\n  ★ 最优: {c1}{op1}{v1} & {c2}{op2}{v2}: n={s[0]}, 20日胜率=100%, 止损率={s[2]:.1f}%, 20日均涨={s[4]:.2f}%')

print(f'\n{"="*80}')
print('  全局最优100%胜率条件（跨形态）')
print(f'{"="*80}')
# 回调>=25% & 一波>=30%
for pb in [20, 22, 25, 28]:
    for w1 in [30, 40, 50]:
        sub = df[(df['pullback_pct'] >= pb) & (df['wave1_gain'] >= w1)]
        s = stats(sub)
        if s and s[1] == 100 and s[0] >= 5:
            print(f'  回调>={pb}% & 一波>={w1}%: n={s[0]}, 20日胜率=100%, 止损率={s[2]:.1f}%, 20日均涨={s[4]:.2f}%')
