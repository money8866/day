# -*- coding: utf-8 -*-
"""
v3.5准确回测：完整股票池扫描
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

test_dates = ['20260624', '20260625', '20260626']

for target_date in test_dates:
    count = 0
    signals = []
    t0 = time.time()
    
    for i, c in enumerate(all_codes):
        c = str(c).zfill(6)
        if c.startswith(('60', '688')):
            code = c + '.SH'
        else:
            code = c + '.SZ'
        
        if code.startswith(('8', '4')) or (code.startswith('9') and code.endswith('.SZ')):
            continue
        
        try:
            result = detector.detect_sideways_pattern(code, today_only=False)
            if result and str(result.get('entry_date', '')) == target_date:
                count += 1
                name = pool_df[pool_df['code'] == int(c[:6])]['name'].values[0] if len(pool_df[pool_df['code'] == int(c[:6])]) > 0 else c
                signals.append({
                    'code': code,
                    'name': name,
                    'score': result['score'],
                    'pullback': result.get('pullback_pct', 0),
                    'surge': result.get('wave1_gain', 0)
                })
        except Exception as e:
            continue
        
        if i % 200 == 0:
            elapsed = time.time() - t0
            print(f"  {target_date} 进度: {i}/{len(all_codes)}, 已找到{count}只, 用时{elapsed:.0f}s")
    
    signals = sorted(signals, key=lambda x: -x['score'])
    elapsed = time.time() - t0
    
    print(f"\n{'='*60}")
    print(f"{target_date}: {count} 只强势横盘信号（全量{len(all_codes)}只）")
    print(f"用时: {elapsed:.0f}s")
    print(f"{'='*60}")
    for i, s in enumerate(signals[:15], 1):
        print(f"  {i}. {s['name']}({s['code']}) 评分{s['score']} 回调{s['pullback']:.1f}% 一波{s['surge']:.1f}%")
    if len(signals) > 15:
        print(f"  ... 还有 {len(signals)-15} 只")
    print()
