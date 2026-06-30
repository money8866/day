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

# today_only=True 模式
r1 = d.detect_sideways_pattern('300773.SZ', today_only=True)
r2 = d.detect_deep_pullback_pattern('300773.SZ', today_only=True)
print(f'强势横盘(today): {"有信号" if r1 else "无信号"}')
print(f'深度回调(today): {"有信号" if r2 else "无信号"}')

# today_only=False 模式
r3 = d.detect_sideways_pattern('300773.SZ', today_only=False)
r4 = d.detect_deep_pullback_pattern('300773.SZ', today_only=False)
print(f'\n强势横盘(全部): {"有信号" if r3 else "无信号"}')
print(f'深度回调(全部): score={r4["score"] if r4 else "无"} entry_date={r4.get("entry_date","N/A") if r4 else "N/A"}')
