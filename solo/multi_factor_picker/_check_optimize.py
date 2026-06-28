# -*- coding: utf-8 -*-
"""
系统测试不同条件组合，找最优解
目标：每天至少1只信号，保持高胜率
"""
import pandas as pd
import numpy as np

df = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\sideways_analysis.csv')
print(f"总样本数: {len(df)}")
print(f"评分≥30: {(df['score']>=30).sum()} 只")
print()

# 定义测试方案
plans = [
    ("基准: 回调3-8%+一波30-50%+评分≥30",
     lambda d: (d['pullback_pct']>=3) & (d['pullback_pct']<8) & (d['wave1_gain']>=30) & (d['wave1_gain']<50) & (d['score']>=30)),
    
    ("方案1: 回调3-8%+一波25-60%+评分≥30",
     lambda d: (d['pullback_pct']>=3) & (d['pullback_pct']<8) & (d['wave1_gain']>=25) & (d['wave1_gain']<60) & (d['score']>=30)),
    
    ("方案2: 回调3-10%+一波25-60%+评分≥30",
     lambda d: (d['pullback_pct']>=3) & (d['pullback_pct']<10) & (d['wave1_gain']>=25) & (d['wave1_gain']<60) & (d['score']>=30)),
    
    ("方案3: 回调2-10%+一波20-60%+评分≥30",
     lambda d: (d['pullback_pct']>=2) & (d['pullback_pct']<10) & (d['wave1_gain']>=20) & (d['wave1_gain']<60) & (d['score']>=30)),
    
    ("方案4: 回调2-10%+一波20-80%+评分≥28",
     lambda d: (d['pullback_pct']>=2) & (d['pullback_pct']<10) & (d['wave1_gain']>=20) & (d['wave1_gain']<80) & (d['score']>=28)),
    
    ("方案5: 回调1-12%+一波20-80%+评分≥25",
     lambda d: (d['pullback_pct']>=1) & (d['pullback_pct']<12) & (d['wave1_gain']>=20) & (d['wave1_gain']<80) & (d['score']>=25)),
    
    ("方案6: 回调3-10%+一波20-60%+评分≥25",
     lambda d: (d['pullback_pct']>=3) & (d['pullback_pct']<10) & (d['wave1_gain']>=20) & (d['wave1_gain']<60) & (d['score']>=25)),
]

for name, cond in plans:
    sub = df[cond(df)].copy()
    n = len(sub)
    if n == 0:
        print(f"--- {name} ---")
        print(f"  信号数: 0")
        print()
        continue
    
    hit20 = sub['hit_20'].mean() * 100
    hit30 = sub['hit_30'].mean() * 100
    hit_stop = sub['hit_stop'].mean() * 100
    avg_max = sub['max_gain'].mean()
    avg_final = sub['final_gain'].mean()
    win_rate = (sub['final_gain'] > 0).mean() * 100
    
    print(f"--- {name} ---")
    print(f"  信号数: {n}只")
    print(f"  +20%胜率: {hit20:.1f}% | +30%胜率: {hit30:.1f}%")
    print(f"  止损率: {hit_stop:.1f}% | 正收益率: {win_rate:.1f}%")
    print(f"  平均最大涨幅: {avg_max:.1f}% | 平均最终收益: {avg_final:.1f}%")
    print(f"  股票: {', '.join(sub['name'].tolist())}")
    print()

print("=" * 60)
print("各维度分布统计:")
print(f"回调深度: {df['pullback_pct'].describe()[['min','25%','50%','75%','max']].to_dict()}")
print(f"一波涨幅: {df['wave1_gain'].describe()[['min','25%','50%','75%','max']].to_dict()}")
print(f"评分: {df['score'].describe()[['min','25%','50%','75%','max']].to_dict()}")
print(f"RSI: {df['rsi'].describe()[['min','25%','50%','75%','max']].to_dict()}")
