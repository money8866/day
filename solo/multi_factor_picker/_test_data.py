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
if df is not None:
    print(f'数据行数: {len(df)}')
    # 最后5行
    print('\n最后5行 close vs close_bfq vs close_qfq:')
    for i in range(-5, 0):
        row = df.iloc[i]
        cb = row.get('close_bfq', 'N/A')
        cq = row.get('close_qfq', 'N/A')
        print(f"  {row['trade_date']}  close={row['close']:.2f}  close_bfq={cb}  close_qfq={cq}")
    
    # 检查6/22附近
    mask = (df['trade_date'] >= '20260618') & (df['trade_date'] <= '20260623')
    sub = df[mask]
    print('\n6/18-6/23 详细:')
    for _, row in sub.iterrows():
        print(f"  {row['trade_date']}  close={row['close']:.2f}  close_bfq={row.get('close_bfq','N/A')}  close_qfq={row.get('close_qfq','N/A')}")
