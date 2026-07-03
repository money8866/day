# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

from multi_factor_picker import wave2_pattern_scanner as scanner
import numpy as np

detector = scanner.WavePatternDetector()

ts_code = '600601.SH'
df = detector.load_data(ts_code, lookback=500)
closes = df['close'].values

# 找6月28日的索引
mask = df['trade_date'].astype(str) == '20260628'
if mask.any():
    idx = mask.idxmax()
    print(f"6月28日索引: {idx}")
    print(f"收盘价: {closes[idx]:.2f}")
    
    ma20_key = [k for k in ['ma_bfq_20', 'ma20', 'ma_20'] if k in df.columns]
    if ma20_key:
        ma20 = df[ma20_key[0]].iloc[idx]
        print(f"MA20: {ma20:.2f}")
        dist = abs(closes[idx] - ma20) / ma20
        print(f"距MA20: {dist:.2%}")
        print(f"超过30%阈值? {dist > 0.30}")

# 直接调用detect_sideways_pattern看详细信息
print("\n6月28日详细检测:")
result = detector.detect_sideways_pattern(ts_code, target_date='20260628')
if result:
    print(f"信号产生！但应该被除权过滤...")
    print(f"entry_date: {result['entry_date']}")
    
    # 找到entry_date对应的行
    entry_mask = df['trade_date'].astype(str) == result['entry_date']
    if entry_mask.any():
        entry_idx = entry_mask.idxmax()
        print(f"entry_date索引: {entry_idx}")
        print(f"entry_date收盘价: {closes[entry_idx]:.2f}")
        
        ma20_key = [k for k in ['ma_bfq_20', 'ma20', 'ma_20'] if k in df.columns]
        if ma20_key:
            ma20 = df[ma20_key[0]].iloc[entry_idx]
            print(f"entry_date的MA20: {ma20:.2f}")
            dist = abs(closes[entry_idx] - ma20) / ma20
            print(f"entry_date距MA20: {dist:.2%}")
            print(f"超过30%阈值? {dist > 0.30}")
