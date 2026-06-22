#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查002747.SZ的主题状态"""

import sys
sys.path.append('d:/mystock/solo')

from tushare_quant import pro, TRADE_DATE, load_history

print(f'当前交易日: {TRADE_DATE}')

# 加载历史数据
history_df = load_history(days=20)
print(f'历史选股共 {len(history_df)} 只')

# 检查002747.SZ的主题信息
mask = history_df['code'] == '002747.SZ'
if mask.any():
    row = history_df[mask].iloc[0]
    print('002747.SZ 信息:')
    print(f'  名称: {row.get("name", "")}')
    print(f'  入库日期: {row.get("date", "")}')
    print(f'  入库价格: {row.get("close", 0)}')
    print(f'  所属主题: {row.get("所属主题", "")}')
    print(f'  所属状态: {row.get("所属状态", "")}')
    print(f'  主题趋势分: {row.get("主题趋势分", 0)}')
    print(f'  主题情绪分: {row.get("主题情绪分", 0)}')
else:
    print('002747.SZ 不在历史数据中')

# 查看主题状态的分布
status_counts = history_df['所属状态'].value_counts()
print(f'\n主题状态分布:')
for status, count in status_counts.items():
    print(f'  {status}: {count}只')
