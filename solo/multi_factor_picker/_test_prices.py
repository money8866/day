# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, r'D:\mystock')
if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break
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
