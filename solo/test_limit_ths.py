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

print("测试 limit_list_ths 接口 - 20260529")
print("="*80)

# 测试涨停池
print("\n1. 涨停池数据:")
try:
    zt_df = pro.limit_list_ths(trade_date='20260529', limit_type='涨停池')
    print(f"涨停数: {len(zt_df)}")
    print(f"列名: {list(zt_df.columns)}")
    if not zt_df.empty:
        print(f"前3行数据:")
        print(zt_df.head(3).to_string())
except Exception as e:
    print(f"获取涨停池失败: {e}")

# 测试跌停池
print("\n2. 跌停池数据:")
try:
    dt_df = pro.limit_list_ths(trade_date='20260529', limit_type='跌停池')
    print(f"跌停数: {len(dt_df)}")
    print(f"列名: {list(dt_df.columns)}")
    if not dt_df.empty:
        print(f"前3行数据:")
        print(dt_df.head(3).to_string())
except Exception as e:
    print(f"获取跌停池失败: {e}")

# 对比 limit_list_d 接口
print("\n3. limit_list_d 接口对比:")
try:
    zt_df_old = pro.limit_list_d(trade_date='20260529', limit_type='U')
    print(f"旧接口涨停数: {len(zt_df_old)}")
    print(f"旧接口列名: {list(zt_df_old.columns)}")
    if not zt_df_old.empty:
        print(f"旧接口前3行数据:")
        print(zt_df_old.head(3).to_string())
except Exception as e:
    print(f"旧接口失败: {e}")
