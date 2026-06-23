#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""正确检查两只股票在东财中的概念/行业归属"""
import sys, json
sys.path.append('d:/mystock/solo')
from theme_trend_sentiment_score import get_dc_members

dc = get_dc_members()
print(f'东财数据共 {len(dc)} 条, 列: {list(dc.columns)}')

for name, code in [('宏和科技', '603256.SH'), ('杰普特', '688025.SH')]:
    m = dc[dc['con_code'] == code]
    print(f'\n=== {name}({code}) ===')
    if m.empty:
        print(f'  ❌ 不在 dc_members 中')
    else:
        print(f'  在 dc_members 中，共 {len(m)} 条记录')
        for _, r in m.iterrows():
            board_code = r['ts_code']
            board_name = r['concept_name']
            is_ind = r.get('is_industry', False)
            print(f'    板块代码={board_code}, 名称="{board_name}", is_industry={is_ind}')
