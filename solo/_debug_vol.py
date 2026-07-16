# -*- coding: utf-8 -*-
"""调试159516在20260610的量能分"""
import os, sys, datetime
sys.path.insert(0, r'd:\mystock\solo')
from dotenv import load_dotenv
load_dotenv(r'd:\mystock\config\.env')
import tushare as ts
import pandas as pd
import numpy as np

ts.set_token(os.getenv('TUSHARE_TOKEN'))
pro = ts.pro_api()

TRADE_DATE = '20260610'
today = datetime.datetime.strptime(TRADE_DATE, '%Y%m%d')
start = (today - datetime.timedelta(days=150)).strftime('%Y%m%d')

# 获取159516数据
df = pro.fund_daily(ts_code='159516.SZ', start_date=start, end_date=TRADE_DATE,
                    fields='ts_code,trade_date,close,vol,amount')
df = df.sort_values('trade_date').reset_index(drop=True)

print(f"159516半导体设备 数据({len(df)}条):")
print(df.tail(10).to_string())
print()

vol = df['vol']
recent_vol_avg = vol.tail(5).mean()
hist_vol_avg = vol.tail(20).mean()
vol_ratio = recent_vol_avg / (hist_vol_avg + 1e-6)

print(f"近5日均量: {recent_vol_avg:.2f}")
print(f"近20日均量: {hist_vol_avg:.2f}")
print(f"量比: {vol_ratio:.4f}")

# 新公式
vol_score_new = 50 + (vol_ratio - 1.0) * 50
vol_score_new = max(0, min(100, vol_score_new))

# 旧公式
vol_score_old = min(vol_ratio * 50, 100)

print(f"\n旧公式 vol_score = min({vol_ratio:.4f} * 50, 100) = {vol_score_old:.2f}")
print(f"新公式 vol_score = 50 + ({vol_ratio:.4f} - 1.0) * 50 = {vol_score_new:.2f}")

# 对比515220煤炭
df2 = pro.fund_daily(ts_code='515220.SH', start_date=start, end_date=TRADE_DATE,
                     fields='ts_code,trade_date,close,vol,amount')
df2 = df2.sort_values('trade_date').reset_index(drop=True)
vol2 = df2['vol']
recent2 = vol2.tail(5).mean()
hist2 = vol2.tail(20).mean()
ratio2 = recent2 / (hist2 + 1e-6)
score2_new = max(0, min(100, 50 + (ratio2 - 1.0) * 50))
score2_old = min(ratio2 * 50, 100)

print(f"\n--- 515220煤炭 ---")
print(f"近5日均量: {recent2:.2f}, 近20日均量: {hist2:.2f}, 量比: {ratio2:.4f}")
print(f"旧公式: {score2_old:.2f}, 新公式: {score2_new:.2f}")
