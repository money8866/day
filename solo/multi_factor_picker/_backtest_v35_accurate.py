# -*- coding: utf-8 -*-
"""
v3.5准确回测：用detect_sideways_pattern扫描5月~6月
"""
import sys, os, time
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import pandas as pd
import numpy as np
import wave2_pattern_scanner as scanner

detector = scanner.WavePatternDetector()

pool_df = pd.read_csv(r'd:\mystock\solo\report_daily\bull_stocks_qualified.csv')
all_codes = pool_df['code'].tolist()

# 选几个有代表性的日期
test_dates = ['20260624', '20260625', '20260626']

for target_date in test_dates:
    count = 0
    signals = []
    
    for i, c in enumerate(all_codes[:400]):  # 先测前400只
        c = str(c).zfill(6)
        if c.startswith(('60', '688')):
            code = c + '.SH'
        else:
            code = c + '.SZ'
        
        if code.startswith(('8', '4')) or (code.startswith('9') and code.endswith('.SZ')):
            continue
        
        try:
            result = detector.detect_sideways_pattern(code, today_only=False)
            if result and result.get('entry_date') == target_date:
                count += 1
                name = pool_df[pool_df['code'] == int(c[:6])]['name'].values[0] if len(pool_df[pool_df['code'] == int(c[:6])]) > 0 else c
                signals.append({
                    'code': code,
                    'name': name,
                    'score': result['score'],
                    'pullback': result.get('pullback_pct', 0),
                    'surge': result.get('wave1_gain_pct', 0)
                })
        except Exception as e:
            continue
    
    signals = sorted(signals, key=lambda x: -x['score'])
    
    print(f"\n{'='*60}")
    print(f"{target_date}: {count} 只强势横盘信号（前400只股票）")
    print(f"{'='*60}")
    for i, s in enumerate(signals[:10], 1):
        print(f"  {i}. {s['name']}({s['code']}) 评分{s['score']} 回调{s['pullback']*100:.1f}% 一波{s['surge']:.1f}%")
    if len(signals) > 10:
        print(f"  ... 还有 {len(signals)-10} 只")
