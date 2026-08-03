# -*- coding: utf-8 -*-
"""20260803 各主题 V2 动量排名"""
import sys; sys.path.insert(0, r'd:\mystock\solo')
import os
from tail_backtest_tdx import load_theme_stocks, parse_tdx_day_file, ts_code_to_tdx_file, calc_theme_momentum_daily

theme_stocks, stock_themes = load_theme_stocks()
all_klines = {}
for code in stock_themes:
    if code.startswith(('9','4')): continue
    tf = ts_code_to_tdx_file(code)
    if tf and os.path.exists(tf):
        df = parse_tdx_day_file(tf)
        if df is not None:
            df = df[df['trade_date'] <= '20260803'].copy()
            if len(df) >= 20:
                all_klines[code] = df

trade_date = '20260803'
theme_momentums = {}
for tn in theme_stocks:
    theme_momentums[tn] = calc_theme_momentum_daily(tn, theme_stocks, all_klines, trade_date)

scores = []
for tn, (up, ar, lr, bc, tm) in theme_momentums.items():
    v2_score = 0
    if up >= 80: v2_score += 7
    elif up >= 60: v2_score += 5
    elif up >= 40: v2_score += 3
    elif up >= 20: v2_score += 1
    if ar > 3: v2_score += 5
    elif ar > 2: v2_score += 4
    elif ar > 1: v2_score += 3
    elif ar > 0: v2_score += 1
    if lr > 5: v2_score += 4
    elif lr > 3: v2_score += 3
    elif lr > 0: v2_score += 2
    if bc >= 80: v2_score += 2
    elif bc >= 50: v2_score += 1
    scores.append((v2_score, up, ar, lr, bc, tn))

scores.sort(reverse=True)
print('20260803 各主题 V2 实时动量排名')
print('')
print('V2总分  上涨比例  均涨幅  龙头涨幅  大涨分  主题')
print('-' * 55)
for v2, up, ar, lr, bc, tn in scores[:15]:
    print(f'  {v2:>2}   {up:>5.1f}%  {ar:>5.2f}%  {lr:>6.2f}%   {bc:>3}   {tn}')