# -*- coding: utf-8 -*-
"""20260803 各主题 V2 详细分数分解"""
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

def calc_sub_scores(up, ar, lr, bc, tm):
    up_score = 0
    if up >= 80: up_score = 7
    elif up >= 60: up_score = 5
    elif up >= 40: up_score = 3
    elif up >= 20: up_score = 1

    ar_score = 0
    if ar > 3: ar_score = 5
    elif ar > 2: ar_score = 4
    elif ar > 1: ar_score = 3
    elif ar > 0: ar_score = 1

    lr_score = 0
    if lr > 5: lr_score = 4
    elif lr > 3: lr_score = 3
    elif lr > 0: lr_score = 2

    bc_score = 0
    if bc >= 80: bc_score = 2
    elif bc >= 50: bc_score = 1

    tm_score = 0
    if tm is not None:
        if tm > 1.0: tm_score = 2
        elif tm > 0.5: tm_score = 1

    return up_score, ar_score, lr_score, bc_score, tm_score

scores = []
for tn, (up, ar, lr, bc, tm) in theme_momentums.items():
    up_s, ar_s, lr_s, bc_s, tm_s = calc_sub_scores(up, ar, lr, bc, tm)
    total = up_s + ar_s + lr_s + bc_s + tm_s
    scores.append((total, up_s, ar_s, lr_s, bc_s, tm_s, up, ar, lr, bc, tn))

scores.sort(reverse=True)

print('20260803 各主题 V2 实时动量 - 详细分数分解')
print('')
print('总分  上涨家数(7)  平均涨幅(5)  龙头表现(4)  大涨股(2)  尾盘(2)  |  上涨比例  均涨幅  龙头涨幅  大涨分  主题')
print('=' * 130)
for total, up_s, ar_s, lr_s, bc_s, tm_s, up, ar, lr, bc, tn in scores[:28]:
    bar = '█' * total + '░' * (20 - total)
    print(f' {total:>2}分 {bar}  {up_s:>1}/{7}      {ar_s:>1}/{5}       {lr_s:>1}/{4}       {bc_s:>1}/{2}      {tm_s:>1}/{2}   |  {up:>5.1f}%  {ar:>5.2f}%  {lr:>6.2f}%   {bc:>3}   {tn}')