# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import wave2_pattern_scanner as scanner

detector = scanner.WavePatternDetector()

code = '001389.SZ'
print("广合科技 (001389.SZ)")
print("="*50)

for d in ['20260624', '20260625', '20260626']:
    result = detector.detect_sideways_pattern(code, today_only=False, target_date=d)
    if result:
        print(f"\n  {d}: ✅ 信号 评分{result['score']} 入场{result['entry_price']:.2f} 回调{result['pullback_pct']:.1f}%")
    else:
        print(f"\n  {d}: ❌ 无信号")
