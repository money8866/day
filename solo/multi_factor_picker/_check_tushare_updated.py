# -*- coding: utf-8 -*-
"""
检查Tushare数据是否已更新到今天
用法：python _check_tushare_updated.py
返回：
  - 如果数据已更新，输出"DATA_UPDATED: YYYYMMDD"并exit(0)
  - 如果未更新，输出"DATA_NOT_UPDATED"并exit(1)
"""
import sys
import datetime
import tushare as ts

# 设置token
token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
ts.set_token(token)
pro = ts.pro_api()

now = datetime.datetime.now()
today_str = now.strftime('%Y%m%d')

print(f"当前时间: {now.strftime('%Y-%m-%d %H:%M')}", file=sys.stderr)

# 检查交易日历
cal = pro.trade_cal(exchange='SSE', start_date=today_str, end_date=today_str)
if cal is not None and len(cal) > 0 and cal.iloc[0]['is_open'] == 1:
    print(f"今天{today_str}是交易日", file=sys.stderr)
else:
    # 今天不是交易日，检查上一个交易日
    cal2 = pro.trade_cal(exchange='SSE', start_date='20260620', end_date=today_str)
    cal2 = cal2[cal2['is_open']==1].sort_values('cal_date', ascending=False)
    if cal2 is not None and len(cal2) > 0:
        last_trade = cal2.iloc[0]['cal_date']
        print(f"今天不是交易日，最近交易日: {last_trade}", file=sys.stderr)
        print(f"DATA_NOT_UPDATED")
        sys.exit(1)
    else:
        print("无法获取交易日历", file=sys.stderr)
        sys.exit(1)

# 检查实际数据是否更新
df = pro.daily(ts_code='600000.SH', start_date=today_str, end_date=today_str)
if df is not None and len(df) > 0:
    print(f"数据已更新到{today_str}", file=sys.stderr)
    print(f"DATA_UPDATED: {today_str}")
    sys.exit(0)
else:
    print(f"数据尚未更新到{today_str}", file=sys.stderr)
    print("DATA_NOT_UPDATED")
    sys.exit(1)
