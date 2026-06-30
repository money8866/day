@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
python -c "
import tushare as ts
import os
for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
        break
pro = ts.pro_api(os.environ['TUSHARE_TOKEN'])
cal = pro.trade_cal(exchange='SSE', start_date='20260620', end_date='20260625')
cal = cal[cal['is_open']==1].sort_values('cal_date', ascending=False)
print('最近交易日:', cal['cal_date'].iloc[0])
df = pro.stk_factor_pro(ts_code='600000.SH', start_date='20260620', end_date='20260625')
if df is not None and len(df) > 0:
    print('stk_factor_pro最新日期:', df['trade_date'].max())
else:
    print('stk_factor_pro无数据')
"
