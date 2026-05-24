#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查最新交易日"""
import tushare as ts
from datetime import datetime, timedelta

TOKEN = 'bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d'
ts.set_token(TOKEN)
pro = ts.pro_api()

# 检查最近5天的交易日历
end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - timedelta(days=7)).strftime('%Y%m%d')

df = pro.trade_cal(exchange='SSE', start_date=start_date, end_date=end_date)
df_open = df[df['is_open'] == 1].sort_values('cal_date', ascending=False)

print('=== 最近7天交易日历 ===')
for _, row in df.iterrows():
    status = 'OPEN' if row['is_open'] == 1 else 'CLOSED'
    print(f'{row["cal_date"]}: {status}')

if len(df_open) > 0:
    latest_trade_date = df_open.iloc[0]['cal_date']
    print(f'\n最新交易日: {latest_trade_date}')
else:
    print('\n⚠️ 最近无交易日')
