# -*- coding: utf-8 -*-
import sys, warnings, tushare as ts
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
pro = ts.pro_api()

codes = ['688213.SH','002317.SZ','300718.SZ','301069.SZ','301029.SZ',
         '688006.SH','603662.SH','000429.SZ','601882.SH','603859.SH','603416.SH']

for c in codes:
    try:
        d = pro.stock_basic(ts_code=c, fields='ts_code,name,industry', timeout=8)
        if d is not None and len(d) > 0:
            print(f"{d.iloc[0]['ts_code']} {d.iloc[0]['name']} | {d.iloc[0].get('industry','')}")
    except Exception as e:
        print(f"{c}: error {e}")
