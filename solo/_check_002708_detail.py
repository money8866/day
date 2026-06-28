# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

from multi_factor_picker import wave2_pattern_scanner as scanner
import numpy as np

detector = scanner.WavePatternDetector()
df = detector.load_data('002708.SZ', lookback=500)
closes = df['close'].values
n = len(df)

# 基于 20260624 截断
mask = df['trade_date'].astype(str) <= '20260624'
df_t = df[mask].copy()

closes_t = df_t['close'].values
nt = len(df_t)

# 找波峰候选
candidates = detector._find_recent_wave1(closes_t, nt, max_lookback=80)

print(f"光洋股份 截止到20260624的波峰候选:")
for i, (h, l, sg) in enumerate(candidates):
    print(f"  候选{i+1}: 波峰日={df_t.iloc[h]['trade_date']} 收盘={closes_t[h]:.2f} 波谷日={df_t.iloc[l]['trade_date']} {closes_t[l]:.2f} 涨幅={sg*100:.1f}%")

# 看第一个候选
h_idx, l_idx, sg = candidates[0]
wave1_high = closes_t[h_idx]
print(f"\n选中波峰: {df_t.iloc[h_idx]['trade_date']} 价={wave1_high:.2f}")
print(f"当前(20260624)收盘: {closes_t[-1]:.2f}")
print(f"距波峰天数: {nt-1-h_idx}天")
print(f"当前/波峰: {closes_t[-1]/wave1_high:.3f}")
print(f"逐波检查 : 最近候选两个波峰")
if len(candidates) >= 2:
    c1_p = closes_t[candidates[0][0]]
    c2_p = closes_t[candidates[1][0]]
    print(f"  最近波峰: {c1_p:.2f}({df_t.iloc[candidates[0][0]]['trade_date']})")
    print(f"  次近波峰: {c2_p:.2f}({df_t.iloc[candidates[1][0]]['trade_date']})")
    print(f"  最近 < 次近(逐波下降)? {c1_p < c2_p}")
