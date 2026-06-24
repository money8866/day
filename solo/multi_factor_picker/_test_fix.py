# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
from wave2_pattern_scanner import WavePatternDetector
d = WavePatternDetector()

# 测试300773（除权股）
r = d.detect_deep_pullback_pattern('300773.SZ')
if r:
    print(f'300773 评分: {r["score"]}')
    print(f'RSI: {r["rsi"]}')
    print(f'entry_price: {r["entry_price"]}')
    print(f'stop_loss: {r["stop_loss"]}')
    for k, v in r["score_details"].items():
        print(f'  {k}: {v}')
else:
    print('300773: 无信号（除权修正后不再满足条件）')

# 测试603929（亚翔集成，非除权）
r2 = d.detect_deep_pullback_pattern('603929.SH')
if r2:
    print(f'\n603929 评分: {r2["score"]}')
    print(f'RSI: {r2["rsi"]}')
else:
    print('\n603929: 无信号')
