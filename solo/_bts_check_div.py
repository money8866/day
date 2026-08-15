# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, r'd:\mystock\solo')
sys.stdout.reconfigure(encoding='utf-8')
from bts.data import parse_tdx_day_file, db_daily

# 比亚迪 TDX vs DB 在 20250722 前后
td = parse_tdx_day_file(r'C:\new_tdx\vipdoc\sz\lday\sz002594.day')
seg = td[(td['trade_date'] >= '20250714') & (td['trade_date'] <= '20250730')]
print('== 比亚迪 TDX 2025-07-14~30 ==')
print(seg[['trade_date', 'open', 'high', 'low', 'close', 'vol']].to_string())
db = db_daily('002594.SZ', '20250714', '20250730')
print('== 比亚迪 DB (同区间) ==')
if db is not None:
    print(db[['trade_date', 'open', 'high', 'low', 'close', 'vol']].to_string())

# 金力泰 20250328
td2 = parse_tdx_day_file(r'C:\new_tdx\vipdoc\sz\lday\sz300225.day')
seg2 = td2[(td2['trade_date'] >= '20250324') & (td2['trade_date'] <= '20250402')]
print('== 金力泰 TDX ==')
print(seg2[['trade_date', 'close']].to_string())
db2 = db_daily('300225.SZ', '20250324', '20250402')
print('== 金力泰 DB ==')
if db2 is not None:
    print(db2[['trade_date', 'close']].to_string())
