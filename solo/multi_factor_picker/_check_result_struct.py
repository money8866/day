# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import wave2_pattern_scanner as scanner

detector = scanner.WavePatternDetector()

# 测试莲花控股
code = '600186.SH'
result = detector.detect_sideways_pattern(code, today_only=False)

if result:
    print("返回字段:")
    for k, v in result.items():
        if k != 'details':
            print(f"  {k}: {v}")
    print(f"\ndetails: {result.get('details', [])[:5]}")
else:
    print("无信号")
