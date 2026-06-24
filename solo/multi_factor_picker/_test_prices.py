# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
from wave2_pattern_scanner import WavePatternDetector
d = WavePatternDetector()
df = d.load_data('300773.SZ', lookback=180)

# 打印完整qfq vs bfq价
print('日期        close(qfq)  close_bfq   差异')
for i in range(0, len(df), 5):
    row = df.iloc[i]
    cb = row.get('close_bfq', 0)
    cq = row.get('close_qfq', row['close'])
    diff_pct = (cb/cq - 1)*100 if cq > 0 else 0
    marker = ' ← 除权' if abs(diff_pct) > 5 else ''
    print(f'{row["trade_date"]}  {row["close"]:>8.2f}  {cb:>10}  {diff_pct:>+.1f}%{marker}')
