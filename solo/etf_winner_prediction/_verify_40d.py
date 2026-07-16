#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证20260515预测的实际收益（按可用最大天数）"""
import pandas as pd
import os

cache = 'd:/mystock/cache_daily'
etfs = ['159870.SZ', '159516.SZ', '159732.SZ', '515880.SH', '516160.SH',
        '515030.SH', '518880.SH', '515180.SH']
names = {'159870.SZ':'氟化工制冷剂','159516.SZ':'半导体设备','159732.SZ':'消费电子',
         '515880.SH':'通信','516160.SH':'新能源','515030.SH':'新能车',
         '518880.SH':'黄金','515180.SH':'红利'}

base_date = '20260515'

# 找到所有ETF共有的最后日期
last_dates = []
for code in etfs:
    fp = os.path.join(cache, f"{code}.csv")
    if not os.path.exists(fp):
        continue
    df = pd.read_csv(fp)
    df['trade_date'] = df['trade_date'].astype(str)
    dates_after = df[df['trade_date'] >= base_date]['trade_date'].tolist()
    if dates_after:
        last_dates.append(dates_after[-1])

# 取共同日期中最早的
common_last = min(last_dates) if last_dates else None
print(f"基准日: {base_date}")
print(f"实际可验证最后日期: {common_last}")

# 计算这是第几个交易日
df_ref = pd.read_csv(os.path.join(cache, f"159516.SZ.csv"))
df_ref['trade_date'] = df_ref['trade_date'].astype(str)
trading_days = df_ref[(df_ref['trade_date'] >= base_date) & (df_ref['trade_date'] <= common_last)]
n_days = len(trading_days)
print(f"距离基准日交易天数: {n_days}")
print("=" * 76)

header = f"{'代码':<12}{'名称':<14}{'基准价':>10}{'最新价':>10}{'实际收益':>10}{'模型预测':>10}"
print(header)
print("-" * 76)

predicted = {'159870.SZ': 12.0, '159516.SZ': 9.5, '159732.SZ': 6.6,
             '515880.SH': 6.4, '516160.SH': 6.2, '515030.SH': 3.6}

results = []
for code in etfs:
    fp = os.path.join(cache, f"{code}.csv")
    if not os.path.exists(fp):
        continue
    df = pd.read_csv(fp)
    df['trade_date'] = df['trade_date'].astype(str)
    base = df[df['trade_date'] == base_date]
    last = df[df['trade_date'] == common_last]
    if base.empty or last.empty:
        continue
    base_close = base['close'].iloc[0]
    last_close = last['close'].iloc[0]
    actual_ret = last_close / base_close - 1
    name = names.get(code, '')
    pred = predicted.get(code, None)
    pred_s = f"{pred:.1f}%" if pred is not None else "-"
    actual_s = f"{actual_ret*100:.1f}%"
    print(f"{code:<12}{name:<14}{base_close:>10.3f}{last_close:>10.3f}{actual_s:>10}{pred_s:>10}")
    results.append((code, name, actual_ret, pred))

print("=" * 76)
print(f"\n按{n_days-1}日实际收益排序:")
print(f"{'排名':<6}{'代码':<12}{'名称':<14}{'实际':>10}{'预测':>10}")
print("-" * 50)
sorted_results = sorted(results, key=lambda x: x[2] if x[2] is not None else -999, reverse=True)
for i, (code, name, r, pred) in enumerate(sorted_results, 1):
    pred_s = f"{pred:.1f}%" if pred is not None else "-"
    print(f"{i:<6}{code:<12}{name:<14}{r*100:.1f}%{pred_s:>10}")
