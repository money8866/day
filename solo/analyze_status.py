#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import tushare as ts
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', '.env'))

pro = ts.pro_api(os.getenv('TUSHARE_TOKEN'))

print("分析 limit_list_ths 接口的 status 字段")
print("="*80)

zt_df = pro.limit_list_ths(trade_date='20260529', limit_type='涨停池')
print(f"\n涨停池总数: {len(zt_df)}")
print(f"\nstatus 字段分布:")
status_counts = zt_df['status'].value_counts().to_dict()
for status, count in status_counts.items():
    print(f"  {status}: {count}")

print(f"\nlimit_up_suc_rate 字段:")
suc_rate = zt_df['limit_up_suc_rate'].value_counts().to_dict()
for rate, count in suc_rate.items():
    print(f"  {rate}: {count}")

# 查看炸板的判断方式
print(f"\n查看前10条数据的状态:")
for i, row in zt_df.head(10).iterrows():
    print(f"  {i+1}. {row['name']} ({row['ts_code']}): status={row['status']}, suc_rate={row['limit_up_suc_rate']}")
