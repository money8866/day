#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查002747.SZ当前价格"""

import sys
sys.path.append('d:/mystock/solo')

from tushare_quant import pro, TRADE_DATE, CACHE_DIR
import os
import pandas as pd

ts_code = '002747.SZ'
cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")

print(f'当前交易日: {TRADE_DATE}')
print(f'缓存文件: {cache_file}')

if os.path.exists(cache_file):
    df = pd.read_csv(cache_file)
    df['trade_date'] = df['trade_date'].astype(str)
    df = df.sort_values('trade_date')
    
    latest = df.iloc[-1]
    current_close = float(latest['close'])
    print(f'当前收盘价: {current_close:.2f}元')
    
    # 计算均线
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma20'] = df['close'].rolling(window=20).mean()
    current_ma5 = float(df.iloc[-1]['ma5'])
    current_ma20 = float(df.iloc[-1]['ma20'])
    print(f'MA5: {current_ma5:.2f}元')
    print(f'MA20: {current_ma20:.2f}元')
    
    # 入库价格
    import sqlite3
    db_path = os.path.join(os.path.dirname(CACHE_DIR), 'report_daily', 'stock_result.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT close FROM stock_pool WHERE code = '002747.SZ' ORDER BY date DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    
    if row:
        last_db_price = row[0]
        print(f'入库价格: {last_db_price:.2f}元')
        print(f'价格上限(+10%): {last_db_price * 1.10:.2f}元')
        if current_close > last_db_price * 1.10:
            print('⚠️ 当前价格超过入库价格10%，会被过滤')
        else:
            print('✅ 当前价格在允许范围内')
    
    # 检查均线条件
    if current_close < current_ma20:
        print('⚠️ 当前价格低于MA20，会被过滤')
    else:
        print('✅ 当前价格高于MA20')
        
    if current_ma5 < current_ma20:
        print('⚠️ MA5低于MA20，会被过滤')
    else:
        print('✅ MA5高于MA20')
else:
    print('缓存文件不存在')
