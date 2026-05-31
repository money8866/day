#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
from dotenv import load_dotenv
load_dotenv("d:/mystock/config/.env")

import tushare as ts
from datetime import datetime, timedelta

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

# 获取最近5个交易日
end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')

cal = pro.trade_cal(exchange='', start_date=start_date, end_date=end_date)
cal = cal[cal['is_open'] == 1]
print("最近5个交易日:")
print(cal.tail(5))

# 检查最近交易日的数据
for trade_date in cal['cal_date'].tail(5).values:
    df = pro.daily_basic(trade_date=trade_date, fields='ts_code,total_mv,turnover_rate')
    print(f"\n{trade_date} 的数据: {len(df)} 条")
    if len(df) > 0:
        print(df.head(3))
        break
