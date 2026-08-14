# -*- coding: utf-8 -*-
import hashlib
import pandas as pd

for f in ['enhanced_timing_bull_all_20260813.csv', 'enhanced_timing_bull_all_20260814.csv']:
    p = r'D:\mystock\solo\report_daily' + '\\' + f
    with open(p, 'rb') as fh:
        h = hashlib.md5(fh.read()).hexdigest()
    df = pd.read_csv(p, encoding='utf-8-sig')
    r = df[df['代码'].astype(str).str.contains('002414')]
    line = f'{f}\n  md5={h}\n  行数={len(df)}'
    if len(r):
        row = r.iloc[0]
        for k in ['现价', '收盘', '最新价', 'trade_date', '收盘价']:
            if k in df.columns:
                line += f'\n  {k}={row[k]}'
    print(line)
