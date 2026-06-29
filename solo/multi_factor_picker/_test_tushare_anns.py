# -*- coding: utf-8 -*-
"""测试Tushare公告接口"""
import tushare as ts
import os

# 读取token
ENV_PATH = r'D:\mystock\config\.env'
with open(ENV_PATH, 'r', encoding='utf-8') as f:
    for line in f:
        if line.startswith('TUSHARE_TOKEN='):
            TOKEN = line.strip().split('=', 1)[1]
            break

pro = ts.pro_api(TOKEN)

print('测试Tushare公告接口...')
print('='*60)

# 方法1: disclosure
print('\n1. pro.disclosure():')
try:
    df = pro.disclosure(ann_date='20260628')
    print(f'   成功！返回 {len(df)} 条记录')
    if len(df) > 0:
        print(f'   列名: {list(df.columns)}')
        print(df.head(2))
except Exception as e:
    print(f'   失败: {str(e)[:100]}')

# 方法2: notice
print('\n2. pro.notice():')
try:
    df = pro.notice(date='20260628')
    print(f'   成功！返回 {len(df)} 条记录')
    if len(df) > 0:
        print(f'   列名: {list(df.columns)}')
        print(df.head(2))
except Exception as e:
    print(f'   失败: {str(e)[:100]}')

# 方法3: anns
print('\n3. pro.anns(ts_code="300802.SZ"):')
try:
    df = pro.anns(ts_code='300802.SZ', start_date='20260621', end_date='20260628')
    print(f'   成功！返回 {len(df)} 条记录')
    if len(df) > 0:
        print(f'   列名: {list(df.columns)}')
        print(df.head(2))
except Exception as e:
    print(f'   失败: {str(e)[:100]}')

# 方法4: finance.corp.cb
print('\n4. finance.corp.cb (可转债公告):')
try:
    df = pro.finance_corp_cb()
    print(f'   成功！返回 {len(df)} 条记录')
    if len(df) > 0:
        print(f'   列名: {list(df.columns)}')
        print(df.head(2))
except Exception as e:
    print(f'   失败: {str(e)[:100]}')

print('\n' + '='*60)
