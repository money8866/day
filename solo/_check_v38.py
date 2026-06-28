# -*- coding: utf-8 -*-
import sys, os, time
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import pandas as pd
from multi_factor_picker import wave2_pattern_scanner as scanner

detector = scanner.WavePatternDetector()

checks = [
    ('002708.SZ', '光洋股份-20260624'),
    ('002708.SZ', '光洋股份-今日'),
    ('002645.SZ', '华宏科技'),
    ('002916.SZ', '深南电路'),
    ('600487.SH', '亨通光电'),
    ('001389.SZ', '广合科技'),
]

for code, name in checks:
    if '20260624' in name:
        r = detector.detect_sideways_pattern(code, target_date='20260624')
    else:
        r = detector.detect_sideways_pattern(code, today_only=True)
    if r:
        print(f"✅ {name}: 评分{r['score']} 调{r['adjust_days']}天 回{r['pullback_pct']:.1f}% 入场{r['entry_price']}")
    else:
        print(f"❌ {name}: 无信号")
