#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""调试跟踪股池问题"""

import sys
sys.path.append('d:/mystock/solo')

from tushare_quant import pro, TRADE_DATE, load_history

print(f'当前交易日: {TRADE_DATE}')
history_df = load_history(days=20)
print(f'历史选股共 {len(history_df)} 只')

# 检查002747.SZ是否在历史数据中
mask = history_df['code'] == '002747.SZ'
if mask.any():
    row = history_df[mask].iloc[0]
    print('002747.SZ 在历史数据中')
    print(f'  入库日期: {row.get("date")}')
    print(f'  入库价格: {row.get("close")}')
else:
    print('002747.SZ 不在历史数据中')

# 查看历史数据中有哪些股票
print(f'\n历史数据前10只股票:')
for i, row in history_df.head(10).iterrows():
    print(f'  {row["code"]} {row["name"]}')
