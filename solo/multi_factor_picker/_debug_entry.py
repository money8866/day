# -*- coding: utf-8 -*-
import os, sys, datetime
sys.path.insert(0, r'D:\mystock')
if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break
import numpy as np
from wave2_pattern_scanner import WavePatternDetector, DEEP_PULLBACK_MIN, DEEP_ADJUST_MIN

d = WavePatternDetector()
df = d.load_data('300773.SZ', lookback=180)
n = len(df)
closes = df['close'].values  # qfq

# 找wave1
candidates = d._find_recent_wave1(closes, n)
for wave1_high_idx, _, surge_gain in candidates:
    wave1_high_price = closes[wave1_high_idx]
    post_high = closes[wave1_high_idx:]
    low_after_high = post_high.min()
    pullback_pct = (wave1_high_price - low_after_high) / wave1_high_price
    low_pos = int(np.argmin(post_high))
    adjust_days = low_pos
    entry_idx = wave1_high_idx + low_pos

    print(f'Wave1: high_idx={wave1_high_idx} date={df.iloc[wave1_high_idx]["trade_date"]} price={wave1_high_price:.2f}(qfq)')
    print(f'  surge_gain={surge_gain*100:.1f}%')
    print(f'  low_after_high={low_after_high:.2f}(qfq)')
    print(f'  pullback_pct={pullback_pct*100:.1f}%')
    print(f'  adjust_days={adjust_days}')
    print(f'  entry_idx={entry_idx} date={df.iloc[entry_idx]["trade_date"]}')
    
    if pullback_pct >= DEEP_PULLBACK_MIN and adjust_days >= DEEP_ADJUST_MIN:
        print(f'  → 满足深度回调条件！')
        row = df.iloc[entry_idx]
        print(f'  row close={row["close"]:.2f} close_bfq={row.get("close_bfq","N/A")}')
        # 这就是entry_price
        ep = float(row.get('close_bfq', row['close']))
        print(f'  entry_price = close_bfq = {ep}')
