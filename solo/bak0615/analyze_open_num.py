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

print("分析 open_num 字段")
print("="*80)

zt_df = pro.limit_list_ths(trade_date='20260529', limit_type='涨停池')
print(f"涨停池总数: {len(zt_df)}")

if 'open_num' in zt_df.columns:
    print(f"\nopen_num 字段分布:")
    open_num_counts = zt_df['open_num'].value_counts().sort_index().to_dict()
    for num, count in open_num_counts.items():
        print(f"  {num}: {count}")
    
    # 计算炸板率
    zt_count = len(zt_df)
    broken_count = len(zt_df[zt_df['open_num'] > 0])
    broken_rate = (broken_count / zt_count * 100) if zt_count > 0 else 0
    print(f"\n炸板统计:")
    print(f"  涨停总数: {zt_count}")
    print(f"  开板数量: {broken_count}")
    print(f"  炸板率: {broken_rate:.1f}%")

# 对比旧接口
print(f"\n旧接口 limit_list_d:")
try:
    zt_df_old = pro.limit_list_d(trade_date='20260529', limit_type='U')
    old_zt_count = len(zt_df_old)
    if 'open_times' in zt_df_old.columns:
        old_broken_count = len(zt_df_old[zt_df_old['open_times'] > 0])
        old_broken_rate = (old_broken_count / old_zt_count * 100) if old_zt_count > 0 else 0
        print(f"  旧接口涨停数: {old_zt_count}")
        print(f"  旧接口炸板数: {old_broken_count}")
        print(f"  旧接口炸板率: {old_broken_rate:.1f}%")
except Exception as e:
    print(f"旧接口失败: {e}")
