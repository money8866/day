# -*- coding: utf-8 -*-
"""验证候选因子的回测数据支撑（dist_ma5 分档 / 突破后延续 / 量能持续性）"""
import sys
sys.path.insert(0, r'd:\mystock\solo')
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd

df = pd.read_csv(r'D:\mystock\solo\output\bts\bts_backtest_20240101_20260814.csv')

def stat(g, label):
    if g.empty or len(g) < 30:
        print(f'{label}: n={len(g)} (样本过少)')
        return
    s5, s20 = g['fut5'].dropna(), g['fut20'].dropna()
    print(f'{label}: n={len(g)} | T+5 {s5.mean():+.2f}% 胜率{(s5>0).mean()*100:.0f}% | '
          f'T+20 {s20.mean():+.2f}% 胜率{(s20>0).mean()*100:.0f}%')

print('=== 因子1: 距MA5分档（博济+8.2%，现>8%已触发扣分）===')
for lo, hi in ((0, 0.02), (0.02, 0.04), (0.04, 0.06), (0.06, 0.08), (0.08, 0.10), (0.10, 0.15)):
    stat(df[(df['dist_ma5'] >= lo) & (df['dist_ma5'] < hi)], f'距MA5 {lo*100:.0f}-{hi*100:.0f}%')

print()
print('=== 因子2: 量能持续性分档（博济3/5，满分为5）===')
for p in range(1, 6):
    stat(df[df['persist'] == p], f'量持续 {p}/5')

print()
print('=== 因子3: 突破后第几日（博济day1）===')
for d in (0, 1, 2, 3, 4, 5):
    stat(df[df['days_after'] == d], f'突破后{d}日')

print()
print('=== 因子4: 突破量比（博济2.09）===')
for lo, hi in ((1.3, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 5.0)):
    stat(df[(df['vol_ratio_bo'] >= lo) & (df['vol_ratio_bo'] < hi)], f'突破量比 {lo}-{hi}')

print()
print('=== 因子5: 平台振幅（博济26.5%）===')
for lo, hi in ((0, 0.15), (0.15, 0.20), (0.20, 0.25), (0.25, 0.30)):
    stat(df[(df['base_range'] >= lo) & (df['base_range'] < hi)], f'平台振幅 {lo*100:.0f}-{hi*100:.0f}%')
