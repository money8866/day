# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8')
import tushare as ts
from pathlib import Path
import pandas as pd

env = Path(r'C:\Users\kongx\mystock\.env').read_text()
for line in env.splitlines():
    if line.startswith('TUSHARE_TOKEN='):
        ts.set_token(line.split('=',1)[1].strip())
        break

pro = ts.pro_api()

codes = ['300489','688008','301328','688433','688059','300586','301031','300199']
names = ['光智科技','澜起科技','维峰电子','华曙高科','华锐精密','美联新材','中熔电气','翰宇药业']
suffixes = ['.SZ','.SH','.SZ','.SH','.SH','.SZ','.SZ','.SZ']

# Financial indicators
print('=== 财务指标 Q1 ===')
for i, code in enumerate(codes):
    ts_code = code + suffixes[i]
    try:
        fi = pro.fina_indicator(ts_code=ts_code, period='20260331', fields='ts_code,roe,yoyprofit,yoy_sales,grossprofit_margin,netprofit_margin,debt_to_assets,op_yoy')
        if len(fi) > 0:
            f = fi.iloc[0]
            print(f"{names[i]}: ROE={f.get('roe','N/A')} 净利YOY={f.get('yoyprofit','N/A')} 营收YOY={f.get('yoy_sales','N/A')} 毛利率={f.get('grossprofit_margin','N/A')} 净利率={f.get('netprofit_margin','N/A')} 负债率={f.get('debt_to_assets','N/A')} 营业利润YOY={f.get('op_yoy','N/A')}")
        else:
            print(f"{names[i]}: 无Q1指标")
    except Exception as e:
        print(f"{names[i]}: err {e}")

# 52周高低
print('\n=== 52周高低 ===')
for i, code in enumerate(codes):
    ts_code = code + suffixes[i]
    try:
        df = pro.daily(ts_code=ts_code, start_date='20250522', end_date='20260522')
        if len(df) > 0:
            high52 = df['high'].max()
            low52 = df['low'].min()
            cur = df.sort_values('trade_date').iloc[-1]['close']
            pct_from_low = (cur - low52) / low52 * 100
            pct_from_high = (cur - high52) / high52 * 100
            pct_position = (cur - low52) / (high52 - low52) * 100
            print(f"{names[i]}: 当前{cur} 52周高{high52} 低{low52} 位置{pct_position:.0f}% 距高{pct_from_high:.1f}% 距低+{pct_from_low:.1f}%")
    except Exception as e:
        print(f"{names[i]}: err {e}")

# 均线
print('\n=== 均线趋势 ===')
for i, code in enumerate(codes):
    ts_code = code + suffixes[i]
    try:
        df = pro.daily(ts_code=ts_code, start_date='20260101', end_date='20260522')
        if len(df) > 0:
            df = df.sort_values('trade_date')
            cur = df.iloc[-1]['close']
            ma5 = df.tail(5)['close'].mean()
            ma10 = df.tail(10)['close'].mean()
            ma20 = df.tail(20)['close'].mean()
            ma60 = df.tail(60)['close'].mean() if len(df) >= 60 else 'N/A'
            trend = '多头' if cur > ma5 > ma10 > ma20 else ('偏多' if cur > ma20 else '偏弱')
            print(f"{names[i]}: 现价{cur} MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f} MA60={ma60 if isinstance(ma60,str) else f'{ma60:.2f}'} 趋势={trend}")
    except Exception as e:
        print(f"{names[i]}: err {e}")
