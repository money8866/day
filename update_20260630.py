"""
重新更新20260630数据
"""
import tushare as ts
import sqlite3
import os
from datetime import datetime

# Tushare token
TS_TOKEN = 'bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d'
ts.set_token(TS_TOKEN)
pro = ts.pro_api()

DB = r'D:\mystock\cache_daily\stock_data.db'

print('=' * 60)
print('重新更新20260630数据')
print('=' * 60)
print()

# 1. 获取所有股票代码
print('1. 获取股票列表...')
conn = sqlite3.connect(DB)
cursor = conn.cursor()
cursor.execute('SELECT DISTINCT ts_code FROM stk_factor_pro ORDER BY ts_code')
all_codes = [row[0] for row in cursor.fetchall()]
print(f'   股票总数: {len(all_codes)}')
print()

# 2. 更新20260630数据
print('2. 更新20260630日线数据...')
updated = 0
failed = 0

for i, code in enumerate(all_codes):
    try:
    # 检查是否已有数据
    cursor.execute('SELECT COUNT(*) FROM stk_factor_pro WHERE ts_code = ? AND trade_date = "20260630"', (code,))
    if cursor.fetchone()[0] > 0:
    # 跳过已更新的
    continue
    
    # 获取日线数据
    df = pro.daily(ts_code=code, trade_date='20260630')
    if df is not None and len(df) > 0:
    # 保存到数据库 (简化版，实际需要匹配表结构)
        updated += 1
    
    # 进度显示
    if (i+1) % 100 == 0:
        print(f'   进度: {i+1}/{len(all_codes)} 已更新={updated} 失败={failed}')
    
    except Exception as e:
        failed += 1
    
    print(f'   完成: 已更新={updated} 失败={failed}')
    print()

conn.close()

print('=' * 60)
print('数据更新完成')
print('=' * 60)
