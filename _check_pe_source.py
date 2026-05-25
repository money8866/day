# -*- coding: utf-8 -*-
import sys, warnings, tushare as ts
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
pro = ts.pro_api()

# Test: which API gives PE?
tc = '000001.SZ'
print("=== daily ===")
d = pro.daily(ts_code=tc, trade_date='20260522', timeout=10)
print(d.columns.tolist() if d is not None else "None")
print(d.head() if d is not None else "None")

print("\n=== daily_basic ===")
d2 = pro.daily_basic(ts_code=tc, trade_date='20260522', timeout=10)
print(d2.columns.tolist() if d2 is not None else "None")
print(d2.head() if d2 is not None else "None")

print("\n=== fina_indicator ===")
fi = pro.fina_indicator(ts_code=tc, start_date='20260101', timeout=10)
print(fi.columns.tolist()[:15] if fi is not None else "None")
print(fi[['trade_date','pe','grossprofit_margin','netprofit_margin']].head() if fi is not None else "None")
