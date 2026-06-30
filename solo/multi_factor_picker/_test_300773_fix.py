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

# 强势横盘
r1 = d.detect_sideways_pattern('300773.SZ')
if r1:
    print(f'强势横盘: score={r1["score"]} rsi={r1["rsi"]} entry={r1["entry_price"]}')
else:
    print('强势横盘: 无信号')

# 深度回调
r2 = d.detect_deep_pullback_pattern('300773.SZ')
if r2:
    print(f'深度回调: score={r2["score"]} rsi={r2["rsi"]} entry={r2["entry_price"]}')
else:
    print('深度回调: 无信号')
