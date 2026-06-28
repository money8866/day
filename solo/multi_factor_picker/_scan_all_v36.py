# -*- coding: utf-8 -*-
"""
扫描20260624~20260626四种形态全部信号（v3.6 支持target_date）
"""
import sys, os, time
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import pandas as pd
import wave2_pattern_scanner as scanner

detector = scanner.WavePatternDetector()

pool_df = pd.read_csv(r'd:\mystock\solo\report_daily\bull_stocks_qualified.csv')
all_codes = pool_df['code'].tolist()

ts_codes = []
for c in all_codes:
    c = str(c).zfill(6)
    if c.startswith(('60', '688')):
        ts_codes.append(c + '.SH')
    else:
        ts_codes.append(c + '.SZ')
ts_codes = [c for c in ts_codes if not (c.startswith(('8', '4')) or (c.startswith('9') and c.endswith('.SZ')))]
print(f"股票池: {len(ts_codes)} 只\n")

for target_date in ['20260624', '20260625', '20260626']:
    t0 = time.time()
    seen_signals = {}
    
    for i, code in enumerate(ts_codes):
        best = None
        for pattern_name, detect_fn in [
            ('强势横盘', detector.detect_sideways_pattern),
            ('深度回调', detector.detect_deep_pullback_pattern),
            ('放量回调', detector.detect_volume_pullback_pattern),
            ('V型急跌', detector.detect_vshape_pattern),
        ]:
            try:
                result = detect_fn(code, today_only=False, target_date=target_date)
                if result:
                    if best is None or result['score'] > best['score']:
                        best = result
            except:
                pass
        
        if best:
            c6 = code[:6]
            name = pool_df[pool_df['code'] == int(c6)]['name'].values[0] if len(pool_df[pool_df['code'] == int(c6)]) > 0 else code
            key = (code, target_date)
            if key not in seen_signals or best['score'] > seen_signals[key]['score']:
                best['name'] = name
                seen_signals[key] = best
        
        if (i + 1) % 300 == 0:
            elapsed = time.time() - t0
            print(f"  {target_date} 进度: {i+1}/{len(ts_codes)} 已找到{len(seen_signals)}只 用时{elapsed:.0f}s")
    
    signals = sorted(seen_signals.values(), key=lambda x: -x['score'])
    elapsed = time.time() - t0
    
    print(f"\n{'='*70}")
    print(f"{target_date}: 共 {len(signals)} 只（四种形态合计，去重后）")
    print(f"用时: {elapsed:.0f}s")
    print(f"{'='*70}")
    
    pattern_counts = {}
    for s in signals:
        p = s.get('pattern', '')
        pattern_counts[p] = pattern_counts.get(p, 0) + 1
    if pattern_counts:
        print(f"形态分布: {pattern_counts}")
    
    for i, s in enumerate(signals, 1):
        print(f"\n【第{i}名】{s.get('name','')} ({s.get('ts_code','')})")
        print(f"  形态: {s.get('pattern','')} | 评分: {s['score']}分")
        print(f"  入场: {s.get('entry_price',0):.2f} | 止损: {s.get('stop_loss',0):.2f} | 目标: {s.get('target',0):.2f}")
        print(f"  回调: {s.get('pullback_pct',0):.1f}% | 一波: {s.get('wave1_gain',0):.1f}%")
    print()
