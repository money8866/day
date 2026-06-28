# -*- coding: utf-8 -*-
"""
调试：看看数据索引格式
"""
import sys, os
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import wave2_pattern_scanner as scanner

detector = scanner.WavePatternDetector()
df = detector.load_data('600519.SH', lookback=100)
print(f"索引类型: {type(df.index)}")
print(f"前5个索引: {df.index[:5].tolist()}")
print(f"后5个索引: {df.index[-5:].tolist()}")
print(f"列名: {df.columns.tolist()[:15]}")
