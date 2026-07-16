#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证动量策略：20天动量最大的ETF，后续收益是否最大"""
import pandas as pd
import os
import numpy as np

cache = 'd:/mystock/cache_daily'

# 所有ETF列表
import yaml
with open('d:/mystock/solo/etf_winner_prediction/config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)
etf_list = list(config.get('etf_universe', {}).keys())
etf_theme = config.get('etf_universe', {})

# 测试多个基准日，验证"20天动量最大 → 60天后收益最大"
test_dates = ['20260515', '20260415', '20260316', '20260216', '20260115', '20251215']

print("=" * 90)
print("动量策略回测：20日涨幅最大ETF → 持有N日收益")
print("=" * 90)

all_results = []

for base_date in test_dates:
    # 计算每个ETF的20日动量
    momentum_data = []
    for code in etf_list:
        fp = os.path.join(cache, f"{code}.csv")
        if not os.path.exists(fp):
            continue
        df = pd.read_csv(fp)
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.sort_values('trade_date').reset_index(drop=True)

        # 找基准日（或之前最近交易日）
        base_idx = df[df['trade_date'] <= base_date].index
        if len(base_idx) == 0:
            continue
        i = base_idx[-1]
        if i < 20:
            continue

        close = df['close'].values.astype(float)
        # 20日动量
        mom_20d = close[i] / close[i-20] - 1
        # 60日动量
        mom_60d = close[i] / close[i-60] - 1 if i >= 60 else 0
        # 基准日收盘
        base_close = close[i]

        # 未来收益（取尽可能多的天数）
        future = df.iloc[i+1:]
        if len(future) < 10:
            continue

        # 取20/40/60交易日收益
        ret_20d = future['close'].iloc[19] / base_close - 1 if len(future) >= 20 else None
        ret_40d = future['close'].iloc[39] / base_close - 1 if len(future) >= 40 else None
        ret_60d = future['close'].iloc[59] / base_close - 1 if len(future) >= 60 else None
        max_avail = len(future)
        ret_max = future['close'].iloc[-1] / base_close - 1

        momentum_data.append({
            'code': code,
            'name': etf_theme.get(code, ''),
            'mom_20d': mom_20d,
            'mom_60d': mom_60d,
            'ret_20d': ret_20d,
            'ret_40d': ret_40d,
            'ret_60d': ret_60d,
            'ret_max': ret_max,
            'max_days': max_avail,
        })

    if not momentum_data:
        continue

    df_mom = pd.DataFrame(momentum_data)

    # 按20日动量排序
    df_mom = df_mom.sort_values('mom_20d', ascending=False).reset_index(drop=True)

    print(f"\n基准日 {base_date}:")
    print(f"{'排名':<4}{'代码':<12}{'名称':<14}{'20日动量':>10}{'60日动量':>10}{'实际20D':>10}{'实际40D':>10}{'实际60D':>10}{'可用天':>8}")
    print("-" * 90)
    for i, row in df_mom.head(10).iterrows():
        r20 = f"{row['ret_20d']*100:.1f}%" if row['ret_20d'] is not None else "N/A"
        r40 = f"{row['ret_40d']*100:.1f}%" if row['ret_40d'] is not None else "N/A"
        r60 = f"{row['ret_60d']*100:.1f}%" if row['ret_60d'] is not None else "N/A"
        print(f"{i+1:<4}{row['code']:<12}{row['name']:<14}{row['mom_20d']*100:>9.1f}%{row['mom_60d']*100:>9.1f}%{r20:>10}{r40:>10}{r60:>10}{row['max_days']:>8}")

    # 统计：Top1动量股 vs 全市场平均收益
    if len(df_mom) >= 5:
        top1 = df_mom.iloc[0]
        top3 = df_mom.head(3)
        all_avg = df_mom

        print(f"\n  Top1动量 {top1['code']}({top1['name']}): 20日动量={top1['mom_20d']*100:.1f}%")
        if top1['ret_60d'] is not None:
            print(f"    60日实际收益: {top1['ret_60d']*100:.1f}%")
        if top1['ret_max'] is not None:
            print(f"    最大可用收益({top1['max_days']}日): {top1['ret_max']*100:.1f}%")

        # Top3平均
        if top3['ret_60d'].notna().any():
            top3_60d = top3['ret_60d'].dropna().mean()
            all_60d = all_avg['ret_60d'].dropna().mean() if all_avg['ret_60d'].notna().any() else None
            print(f"  Top3平均60日收益: {top3_60d*100:.1f}%")
            if all_60d is not None:
                print(f"  全市场平均60日收益: {all_60d*100:.1f}%")
                print(f"  超额收益: {(top3_60d-all_60d)*100:.1f}%")

    # 记录Top1的结果
    top1 = df_mom.iloc[0]
    all_results.append({
        'date': base_date,
        'top1_code': top1['code'],
        'top1_name': top1['name'],
        'top1_mom': top1['mom_20d'],
        'top1_ret60': top1['ret_60d'],
        'top1_ret_max': top1['ret_max'],
        'top1_days': top1['max_days'],
    })

print("\n" + "=" * 90)
print("汇总：每个基准日的Top1动量ETF表现")
print("=" * 90)
print(f"{'基准日':<12}{'代码':<12}{'名称':<14}{'20日动量':>10}{'实际60D':>10}{'可用天':>8}")
print("-" * 70)
for r in all_results:
    r60 = f"{r['top1_ret60']*100:.1f}%" if r['top1_ret60'] is not None else "N/A"
    print(f"{r['date']:<12}{r['top1_code']:<12}{r['top1_name']:<14}{r['top1_mom']*100:>9.1f}%{r60:>10}{r['top1_days']:>8}")
