# -*- coding: utf-8 -*-
"""验证板块形态适配加分"""
import os, sys
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
from wave2_pattern_scanner import WavePatternDetector

d = WavePatternDetector()

# 主板强势横盘 (600183.SH 生益科技)
r = d.detect_sideways_pattern('600183.SH', today_only=True)
if r:
    print(f'600183 主板强势横盘: score={r["score"]}')
    print(f'  评分细节: {r["score_details"]}')
else:
    print('600183: 无强势横盘信号')

# 双创深度回调 (688787.SH 海天瑞声)
r = d.detect_deep_pullback_pattern('688787.SH', today_only=True)
if r:
    print(f'\n688787 双创深度回调: score={r["score"]}')
    print(f'  评分细节: {r["score_details"]}')
else:
    print('\n688787: 无深度回调信号')

# 主板深度回调 (603929.SH 亚翔集成)
r = d.detect_deep_pullback_pattern('603929.SH', today_only=True)
if r:
    print(f'\n603929 主板深度回调: score={r["score"]}')
    print(f'  评分细节: {r["score_details"]}')
else:
    print('\n603929: 无深度回调信号')

# 双创强势横盘 (688519.SH)
r = d.detect_sideways_pattern('688519.SH', today_only=True)
if r:
    print(f'\n688519 双创强势横盘: score={r["score"]}')
    print(f'  评分细节: {r["score_details"]}')
else:
    print('\n688519: 无强势横盘信号')
