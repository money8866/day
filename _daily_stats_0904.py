# -*- coding: utf-8 -*-
import tushare as ts, urllib.request, json

pro = ts.pro_api('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

# 涨跌停
zt = pro.limit_list_d(trade_date='20260903', limit_type='U')
dt = pro.limit_list_d(trade_date='20260903', limit_type='D')
print(f'涨停: {len(zt)}家  跌停: {len(dt)}家')

# 昨日收盘强势板块 from tushare concept
sector = pro.index_classify(level='L1', src='SW')
print('SW行业数量:', len(sector))
