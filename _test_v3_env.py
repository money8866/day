# -*- coding: utf-8 -*-
import os, sys, datetime, warnings, sqlite3
import tushare as ts
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
pro = ts.pro_api()

print('测试Tushare连接...')
try:
    df = pro.daily(trade_date='20260522', timeout=15)
    print(f'daily OK: {len(df)} rows')
except Exception as e:
    print(f'daily failed: {e}')

try:
    conn = sqlite3.connect(r'C:\Users\kongx\mystock\hot_sector.db')
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = c.fetchall()
    print(f'db tables: {tables}')
    c.execute("SELECT MAX(date) FROM hot_sector")
    print(f'latest date: {c.fetchone()}')
    c.execute("SELECT l2_name, momentum FROM hot_sector WHERE date='20260522' ORDER BY momentum DESC LIMIT 5")
    print(f'0522 sectors: {c.fetchall()}')
    conn.close()
except Exception as e:
    print(f'db error: {e}')
