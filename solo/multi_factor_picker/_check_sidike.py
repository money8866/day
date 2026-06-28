# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import wave2_pattern_scanner as scanner

detector = scanner.WavePatternDetector()

code = '300806.SZ'
name = '斯迪克'

print(f"=== {name} ({code}) 四形态检测 ===")

patterns = [
    ('sideways', '强势横盘'),
    ('deep', '深度回调'),
    ('volume', '放量回调'),
    ('vshape', 'V型急跌'),
]

for pat, pat_name in patterns:
    if pat == 'sideways':
        r = detector.detect_sideways_pattern(code, today_only=True)
    elif pat == 'deep':
        r = detector.detect_deep_pullback_pattern(code, today_only=True)
    elif pat == 'volume':
        r = detector.detect_volume_pullback_pattern(code, today_only=True)
    elif pat == 'vshape':
        r = detector.detect_vshape_pattern(code, today_only=True)
    
    if r:
        print(f"\n  ✅ {pat_name}: {r['score']}分")
        print(f"     回调: {r['pullback_pct']}%  一波: {r['wave1_gain']}%  入场价: {r['entry_price']:.2f}")
        print(f"     评分明细: {r.get('score_details', '')[:80]}...")
    else:
        print(f"  ❌ {pat_name}: 无信号")

# 检查是否在bull池里
import pandas as pd
bull_csv = r'D:\mystock\solo\report_daily\bull_stocks_qualified.csv'
try:
    df_bull = pd.read_csv(bull_csv)
    if code in df_bull['code'].values:
        print(f"\n✅ {name} 在 bull 池中")
    else:
        print(f"\n❌ {name} 不在 bull 池中")
except Exception as e:
    print(f"\n读取bull池失败: {e}")
