# -*- coding: utf-8 -*-
"""
扫描20260624~20260626四种形态全部信号
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
    seen_signals = {}  # 去重
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
            # 四种形态都检测
            for pattern_name, detect_fn in [
                ('强势横盘', detector.detect_sideways_pattern),
                ('深度回调', detector.detect_deep_pullback_pattern),
                ('放量回调', detector.detect_volume_pullback_pattern),
                ('V型急跌', detector.detect_vshape_pattern),
            ]:
                try:
                    result = detect_fn(code, today_only=False)
                    if result and str(result.get('entry_date', '')) == target_date:
                        name = pool_df[pool_df['code'] == int(c[:6])]['name'].values[0] if len(pool_df[pool_df['code'] == int(c[:6])]) > 0 else c
                        signal = {
                            'code': code,
                            'name': name,
                            'pattern': result.get('pattern', pattern_name),
                            'score': result['score'],
                            'pullback': result.get('pullback_pct', 0),
                            'surge': result.get('wave1_gain', 0),
                            'entry_price': result.get('entry_price', 0),
                            'stop_loss': result.get('stop_loss', 0),
                            'target': result.get('target', 0),
                            'details': result.get('score_details', '')[:80]
                        }
                        key = (code, target_date)
                        if key not in seen_signals or signal['score'] > seen_signals[key]['score']:
                            seen_signals[key] = signal
                        break  # 同一只股票同一天只保留评分最高的形态
                except:
                    pass
        except:
            continue
        
        if i % 300 == 0:
            print(f"  {target_date} 进度: {i}/{len(all_codes)}")
    
    signals = sorted(seen_signals.values(), key=lambda x: -x['score'])
    elapsed = time.time() - t0
    
    print(f"\n{'='*70}")
    print(f"{target_date}: 共 {len(signals)} 只信号（四种形态合计，去重后）")
    print(f"用时: {elapsed:.0f}s")
    print(f"{'='*70}")
    
    if signals:
        for i, s in enumerate(signals, 1):
            print(f"\n【第{i}名】{s['name']} ({s['code']})")
            print(f"  形态: {s['pattern']} | 评分: {s['score']}分")
            print(f"  入场价: {s['entry_price']:.2f} | 止损: {s['stop_loss']:.2f} | 目标: {s['target']:.2f}")
            print(f"  回调: {s['pullback']:.1f}% | 一波: {s['surge']:.1f}%")
    else:
        print("\n当天无信号")
    print()
