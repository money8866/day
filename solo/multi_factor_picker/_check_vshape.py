# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import wave2_pattern_scanner as scanner

detector = scanner.WavePatternDetector()

code = '300221.SZ'
name = '银禧科技'

result = detector.detect_vshape_pattern(code, today_only=False, target_date='20260626')
if result:
    print(f"{name} ({code}) - V型急跌信号")
    print("="*60)
    for k, v in result.items():
        if k not in ('details', 'score_details'):
            print(f"  {k}: {v}")
    print(f"\n  score_details: {result.get('score_details', '')}")
else:
    print(f"{name} ({code}) - 无信号")
