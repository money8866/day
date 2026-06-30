# -*- coding: utf-8 -*-
import os, sys, datetime
sys.path.insert(0, r'D:\mystock')
if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break
import numpy as np
from wave2_pattern_scanner import WavePatternDetector

d = WavePatternDetector()
df = d.load_data('300773.SZ', lookback=180)
n = len(df)
closes = df['close'].values  # 现在是qfq

# 找wave1
candidates = d._find_recent_wave1(closes, n)
print(f'Wave1 candidates: {len(candidates)}')
for hi, lo, gain in candidates[:5]:
    print(f'  high_idx={hi} date={df.iloc[hi]["trade_date"]} close={closes[hi]:.2f}  gain={gain*100:.1f}%')

# 手动模拟深度回调检测
for wave1_high_idx, _, surge_gain in candidates:
    wave1_high_price = closes[wave1_high_idx]
    post_high = closes[wave1_high_idx:]
    low_after_high = post_high.min()
    pullback_pct = (wave1_high_price - low_after_high) / wave1_high_price
    low_pos = int(np.argmin(post_high))
    adjust_days = low_pos
    entry_idx = wave1_high_idx + low_pos
    
    if entry_idx >= n:
        continue
    if entry_idx == n - 1:  # today_only
        print(f'\n=== TODAY SIGNAL ===')
        print(f'  wave1_high: {df.iloc[wave1_high_idx]["trade_date"]} price={wave1_high_price:.2f}')
        print(f'  low_after_high: {low_after_high:.2f} (qfq)')
        print(f'  pullback_pct: {pullback_pct*100:.1f}%')
        print(f'  entry_idx: {entry_idx} date={df.iloc[entry_idx]["trade_date"]}')
        
        row = df.iloc[entry_idx]
        print(f'  close(qfq): {row["close"]:.2f}')
        print(f'  close_bfq: {row.get("close_bfq", "N/A")}')
