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

print('=== 最新行情 ===')
for i, code in enumerate(codes):
    ts_code = code + suffixes[i]
    df = pro.daily(ts_code=ts_code, start_date='20260519', end_date='20260522')
    if len(df) > 0:
        row = df.sort_values('trade_date').iloc[-1]
        print(f"{names[i]}({code}): close={row['close']} pct={row['pct_chg']:.2f}% vol={row['amount']/1000:.1f}千万 turnover={row.get('turnover_rate','N/A')}")

print('\n=== 基本面数据 ===')
for i, code in enumerate(codes):
    ts_code = code + suffixes[i]
    try:
        basic = pro.daily_basic(ts_code=ts_code, start_date='20260522', end_date='20260522')
        if len(basic) > 0:
            b = basic.iloc[0]
            print(f"{names[i]}: PE={b.get('pe','N/A')} PB={b.get('pb','N/A')} 总市值={b.get('total_mv','N/A')}亿 流通市值={b.get('circ_mv','N/A')}亿")
        else:
            # try earlier date
            basic = pro.daily_basic(ts_code=ts_code, start_date='20260519', end_date='20260522')
            if len(basic) > 0:
                b = basic.sort_values('trade_date').iloc[-1]
                print(f"{names[i]}: PE={b.get('pe','N/A')} PB={b.get('pb','N/A')} 总市值={b.get('total_mv','N/A')}亿 流通市值={b.get('circ_mv','N/A')}亿")
            else:
                print(f"{names[i]}: 无基本面数据")
    except Exception as e:
        print(f"{names[i]}: 错误 {e}")

print('\n=== Q1财务数据 ===')
for i, code in enumerate(codes):
    ts_code = code + suffixes[i]
    try:
        fin = pro.income(ts_code=ts_code, period='20260331', fields='ts_code,revenue,revenue_yoy,nprofit,nprofit_yoy,grossprofit_margin,netprofit_margin')
        if len(fin) > 0:
            f = fin.iloc[0]
            print(f"{names[i]}: 营收={f.get('revenue','N/A')}亿 营收YOY={f.get('revenue_yoy','N/A')}% 净利={f.get('nprofit','N/A')}亿 净利YOY={f.get('nprofit_yoy','N/A')}% 毛利率={f.get('grossprofit_margin','N/A')}% 净利率={f.get('netprofit_margin','N/A')}%")
        else:
            print(f"{names[i]}: 无Q1财务数据")
    except Exception as e:
        print(f"{names[i]}: 错误 {e}")

print('\n=== 资产负债 ===')
for i, code in enumerate(codes):
    ts_code = code + suffixes[i]
    try:
        bal = pro.balancesheet(ts_code=ts_code, period='20260331', fields='ts_code,total_assets,total_liab,total_hldr_eqy_exc_min_int,debt_to_assets')
        if len(bal) > 0:
            b = bal.iloc[0]
            print(f"{names[i]}: 总资产={b.get('total_assets','N/A')}亿 负债={b.get('total_liab','N/A')}亿 资产负债率={b.get('debt_to_assets','N/A')}%")
        else:
            print(f"{names[i]}: 无资产负债数据")
    except Exception as e:
        print(f"{names[i]}: 错误 {e}")
