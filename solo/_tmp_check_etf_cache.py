# -*- coding: utf-8 -*-
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import pandas as pd

# 1. 缓存文件 512480.SH 和 515030.SH 最近10行
for ts in ['512480.SH', '515030.SH', '159667.SZ', '159995.SZ']:
    p = r'd:\mystock\cache_daily\etf_fund\{ts}_20260803.csv'.replace('{ts}', ts)
    if os.path.exists(p):
        df = pd.read_csv(p)
        print(f'=== {ts} 缓存 {len(df)} 行，最后6行 ===')
        print(df.tail(6).to_string(index=False))
        print()
    else:
        print(f'{ts} 缓存不存在: {p}')
        print()
