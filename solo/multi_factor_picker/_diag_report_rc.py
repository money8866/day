# -*- coding: utf-8 -*-
"""诊断 report_rc 数据覆盖问题"""
import sys
sys.path.insert(0, '.')
import tushare as ts
from main import load_config, get_token
from datetime import datetime, timedelta
import pandas as pd

config = load_config()
token = get_token(config)
pro = ts.pro_api(token=token)

print('=== 测试不同参数组合 ===')

# 1. 按ann_date单日
day = '20260622'
df1 = pro.report_rc(ann_date=day)
print(f'1. ann_date={day}: {len(df1)}条, 去重股数={df1["ts_code"].nunique()}')

# 2. 无参数
try:
    df2 = pro.report_rc()
    print(f'2. 无参数: {len(df2)}条, 去重={df2["ts_code"].nunique()}')
except Exception as e:
    print(f'2. 无参数: 错误 {e}')

# 3. 按report_date范围
try:
    df3 = pro.report_rc(start_date='20260101', end_date='20260622')
    print(f'3. report_date范围: {len(df3)}条, 去重={df3["ts_code"].nunique()}')
    if len(df3) > 0:
        print('   quarter分布:', df3['quarter'].value_counts().head(10).to_dict())
        top_stock = df3['ts_code'].value_counts().idxmax()
        print(f'   覆盖最多的股票: {top_stock}, {df3["ts_code"].value_counts().max()}条')
        sub = df3[df3['ts_code'] == top_stock]
        print(sub[['ts_code', 'report_date', 'ann_date', 'quarter', 'org_name', 'eps', 'np', 'rating']].to_string(index=False))
except Exception as e:
    print(f'3. report_date范围: 错误 {e}')

# 4. 按ts_code单只查
print('\n=== 按单只股票查询历史研报 ===')
for code in ['300308.SZ', '688256.SH', '603893.SH']:
    try:
        df4 = pro.report_rc(ts_code=code)
        if df4 is not None and len(df4) > 0:
            print(f'{code}: {len(df4)}条研报, quarter={df4["quarter"].value_counts().to_dict()}')
            print(df4[['ts_code', 'ann_date', 'report_date', 'quarter', 'org_name', 'eps', 'np', 'rating']].to_string(index=False))
        else:
            print(f'{code}: 无数据')
    except Exception as e:
        print(f'{code}: 错误 {e}')
    print()
