# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import wave2_pattern_scanner as scanner

detector = scanner.WavePatternDetector()

code = '001389.SZ'
name = '广合科技'

result = detector.detect_sideways_pattern(code, today_only=False)
if result:
    print(f"{name} ({code}) - 信号详情")
    print("="*60)
    for k, v in result.items():
        if k != 'details' and k != 'score_details':
            print(f"  {k}: {v}")
    print(f"\n  score_details: {result.get('score_details', '')[:120]}")
else:
    print(f"{name} ({code}) - 无信号")
