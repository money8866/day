"""
诊断：检查数据库是否有今日(20260630)数据
"""
import sqlite3
import os

DB = r'D:\mystock\cache_daily\stock_data.db'

print('=' * 60)
print('数据库诊断')
print('=' * 60)
print()

if not os.path.exists(DB):
    print(f'❌ 数据库不存在: {DB}')
    exit()

conn = sqlite3.connect(DB)
cursor = conn.cursor()

# 1. 检查最新日期
print('1. 数据库中最新日期:')
cursor.execute('SELECT DISTINCT trade_date FROM stk_factor_pro ORDER BY trade_date DESC LIMIT 5')
dates = cursor.fetchall()
for d in dates:
    print(f'   {d[0]}')
print()

# 2. 检查20260630数据量
print('2. 20260630数据量:')
cursor.execute("SELECT COUNT(*) FROM stk_factor_pro WHERE trade_date = '20260630'")
count = cursor.fetchone()[0]
print(f'   {count}条')
if count > 0:
    print('   ✅ 今日数据已更新')
else:
    print('   ❌ 今日数据未更新')
print()

# 3. 检查一只股票今日是否有数据
print('3. 检查600460.SH今日是否有数据:')
cursor.execute("SELECT trade_date, close, rsi_bfq_6 FROM stk_factor_pro WHERE ts_code = '600460.SH' AND trade_date = '20260630'")
row = cursor.fetchone()
if row:
    print(f'   日期: {row[0]}, 收盘: {row[1]}, RSI6: {row[2]}')
else:
    print('   无数据')
print()

conn.close()
print('=' * 60)
