# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

from multi_factor_picker import wave2_pattern_scanner as scanner
import numpy as np

detector = scanner.WavePatternDetector()

# 光洋股份 20260624 检查
r24 = detector.detect_sideways_pattern('002708.SZ', target_date='20260624')
print(f"光洋股份 20260624: {'✅ 有信号' if r24 else '❌ 无信号'}")

# 今天检查
rt = detector.detect_sideways_pattern('002708.SZ', today_only=True)
print(f"光洋股份 今日:     {'✅ 有信号' if rt else '❌ 无信号'}")

# 其他形态的检查（确保未误伤）
for code, name in [('002916.SZ', '深南电路'), ('600487.SH', '亨通光电')]:
    r = detector.detect_sideways_pattern(code, today_only=True)
    print(f"{name} ({code}): {'✅ 评分'+str(r['score']) if r else '❌ 无信号'}")
