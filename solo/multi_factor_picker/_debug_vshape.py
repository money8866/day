# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import wave2_pattern_scanner as scanner
import pandas as pd
import numpy as np

detector = scanner.WavePatternDetector()

code = '300221.SZ'

print("=== today_only=True ===")
r = detector.detect_vshape_pattern(code, today_only=True)
if r:
    print(f"评分: {r['score']}  入场日: {r['entry_date']}  入场价: {r['entry_price']:.2f}")
else:
    print("无信号")

print("\n=== today_only=False ===")
r = detector.detect_vshape_pattern(code, today_only=False)
if r:
    print(f"评分: {r['score']}  入场日: {r['entry_date']}  入场价: {r['entry_price']:.2f}")
else:
    print("无信号")

print("\n=== target_date='20260626' ===")
r = detector.detect_vshape_pattern(code, today_only=False, target_date='20260626')
if r:
    print(f"评分: {r['score']}  入场日: {r['entry_date']}  入场价: {r['entry_price']:.2f}")
else:
    print("无信号")
