# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, r"d:\mystock\solo")
import pandas as pd
from datetime import datetime

ETF_CONS_DIR = r"D:\mystock\cache_daily\etf_cons"

# 读取已缓存的成份股
cons_fp = os.path.join(ETF_CONS_DIR, "cons_588170_SH_20260430.csv")
cons_df = pd.read_csv(cons_fp)
print(f"成份股: {len(cons_df)} 行")
print(f"列: {cons_df.columns.tolist()}")

code_col = None
for c in ['con_code', 'ts_code', 'symbol']:
    if c in cons_df.columns:
        code_col = c
        break
print(f"code_col = {code_col}")

count_ok = 0
count_fail = 0
for i, row in cons_df.head(5).iterrows():
    con_code = str(row[code_col]).strip()
    print(f"\n[{i}] con_code='{con_code}'")
    
    if '.' in con_code:
        con_ts = con_code
    else:
        if con_code.startswith('6') or con_code.startswith('9'):
            con_ts = f"{con_code}.SH"
        elif con_code.startswith('0') or con_code.startswith('3'):
            con_ts = f"{con_code}.SZ"
        else:
            print(f"    skip: 未知后缀")
            continue
    
    print(f"    con_ts='{con_ts}'")
    
    cache_fp = os.path.join(ETF_CONS_DIR, f"stock_{con_ts.replace('.','_')}_20260430.csv")
    print(f"    cache_exists={os.path.exists(cache_fp)}: {cache_fp}")
    
    if os.path.exists(cache_fp):
        sdf = pd.read_csv(cache_fp)
        sdf['trade_date'] = pd.to_datetime(sdf['trade_date'], format="%Y%m%d")
        sdf = sdf.sort_values('trade_date').reset_index(drop=True)
        sdf = sdf[sdf['trade_date'] <= pd.to_datetime("20260430", format="%Y%m%d")]
        print(f"    len(sdf)={len(sdf)}, >=25? {len(sdf) >= 25}")
        if len(sdf) >= 25:
            count_ok += 1
        else:
            count_fail += 1
    else:
        count_fail += 1

print(f"\ncount_ok={count_ok}, count_fail={count_fail}")
