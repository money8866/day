#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查002747.SZ过滤情况"""

import sys
sys.path.append('d:/mystock/solo')

from tushare_quant import pro, TRADE_DATE, CACHE_DIR, BASE_DIR, calc_unified_stock_score, get_hist_data, calc_tech_indicators
import os
import pandas as pd

ts_code = '002747.SZ'

print(f'当前交易日: {TRADE_DATE}')

# 检查缓存文件
cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
old_cache_file = os.path.join(os.path.dirname(BASE_DIR), "cache_daily", f"{ts_code}.csv")

print(f'缓存文件: {cache_file}')
print(f'旧缓存文件: {old_cache_file}')

if os.path.exists(cache_file):
    print('✅ 缓存文件存在')
    df = pd.read_csv(cache_file)
    df['trade_date'] = df['trade_date'].astype(str)
    df = df.sort_values('trade_date')
    print(f'K线数据天数: {len(df)}')
    if len(df) >= 20:
        print('✅ K线数据足够(>=20天)')
    else:
        print('⚠️ K线数据不足20天')
elif os.path.exists(old_cache_file):
    print('✅ 旧缓存文件存在')
else:
    print('❌ 缓存文件不存在')
    
# 计算整合评分
try:
    df_hist = get_hist_data(ts_code)
    if df_hist is not None and len(df_hist) >= 60:
        print('✅ 历史数据足够')
        integrated_score, recommendation, details, failure_prob = calc_unified_stock_score(df_hist, ts_code)
        print(f'整合评分: {integrated_score:.1f}')
        print(f'失败概率: {failure_prob:.1f}%')
        if failure_prob > 50:
            print('⚠️ 失败概率>50%，会被过滤')
        else:
            print('✅ 失败概率<=50%')
    else:
        print('⚠️ 历史数据不足60天')
except Exception as e:
    print(f'❌ 整合评分计算失败: {e}')
