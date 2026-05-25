# -*- coding: utf-8 -*-
import os, sys, datetime, warnings, sqlite3, pickle
import numpy as np
import tushare as ts

sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
pro = ts.pro_api()

# Test fallback: 申万行业动量
print("测试申万行业动量 fallback...")
today = datetime.date.today().strftime('%Y%m%d')
sw_codes = [f'{i:02d}0000.SI' for i in range(1, 32)]
scores = {}
for code in sw_codes[:5]:  # 先测5个
    try:
        d = pro.index_daily(ts_code=code,
            start_date=(datetime.datetime.now()-datetime.timedelta(days=25)).strftime('%Y%m%d'),
            end_date=today, timeout=10)
        if d is not None and len(d) > 0:
            scores[code] = d['pct_chg'].sum()
            print(f"  {code}: {d['pct_chg'].sum():.2f}%")
    except Exception as e:
        print(f"  {code}: error {e}")
