# -*- coding: utf-8 -*-
import os, sys, datetime
import pandas as pd
import numpy as np
import tushare as ts

sys.stdout.reconfigure(encoding='utf-8')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
pro = ts.pro_api('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

today = datetime.date.today().strftime('%Y%m%d')

# 中证2000: ts_code = 932000.CSI
# 中证1000: 000852.SH
# 国证2000: 399303.SZ
indices = [
    ('932000.CSI', '中证2000'),
    ('000852.SH', '中证1000'),
    ('399303.SZ', '国证2000'),
]

for ts_code, name in indices:
    try:
        df = pro.index_daily(ts_code=ts_code, start_date='20240101', end_date=today)
        if df is not None and len(df) > 50:
            print(f"✅ {name}({ts_code}): {len(df)}条")
            print(f"   最新: {df.iloc[0]['trade_date']}  收{df.iloc[0]['close']:.2f}")
            print(f"   最高: {df['close'].max():.2f}  日期:{df.loc[df['close'].idxmax(),'trade_date']}")
            break
        else:
            print(f"❌ {name}({ts_code}): 数据不足")
    except Exception as e:
        print(f"❌ {name}({ts_code}): {e}")
