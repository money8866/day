# -*- coding: utf-8 -*-
"""验证603163评分细节"""
import os, sys
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'

# 直接调用detector
from wave2_pattern_scanner import WavePatternDetector
detector = WavePatternDetector()

# 603163
result = detector.detect_deep_pullback_pattern('603163.SH')
if result:
    print(f"603163 评分: {result['score']}")
    print(f"评分细节: {result['score_details']}")
else:
    print("603163 无深度回调信号")

# 688981
result2 = detector.detect_sideways_pattern('688981.SH')
if result2:
    print(f"\n688981 评分: {result2['score']}")
    print(f"评分细节: {result2['score_details']}")

# 603929
result3 = detector.detect_sideways_pattern('603929.SH')
if result3:
    print(f"\n603929 评分: {result3['score']}")
    print(f"评分细节: {result3['score_details']}")
