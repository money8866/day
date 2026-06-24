# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
from wave2_pattern_scanner import WavePatternDetector
d = WavePatternDetector()

for code in ['603929.SH', '600601.SH', '603678.SH', '600301.SH']:
    r1 = d.detect_sideways_pattern(code, today_only=False)
    r2 = d.detect_deep_pullback_pattern(code, today_only=False)
    if r1:
        print(f'{code} 强势横盘: score={r1["score"]}')
    if r2:
        print(f'{code} 深度回调: score={r2["score"]}')
    if not r1 and not r2:
        print(f'{code}: 无信号')
