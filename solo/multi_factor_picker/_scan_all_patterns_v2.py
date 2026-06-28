# -*- coding: utf-8 -*-
"""
扫描20260624~20260626四种形态全部信号
不用today_only，改为按entry_date过滤
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

ts_codes = []
for c in all_codes:
    c = str(c).zfill(6)
    if c.startswith(('60', '688')):
        ts_codes.append(c + '.SH')
    else:
        ts_codes.append(c + '.SZ')

# 过滤北交所
ts_codes = [c for c in ts_codes if not (c.startswith(('8', '4')) or (c.startswith('9') and c.endswith('.SZ')))]

print(f"股票池: {len(ts_codes)} 只")
print(f"开始扫描全量（不用today_only）...")

t0 = time.time()
all_signals = []

for i, code in enumerate(ts_codes):
    if (i + 1) % 100 == 0:
        elapsed = time.time() - t0
        eta = elapsed / (i+1) * (len(ts_codes) - i - 1)
        print(f"  进度 {i+1}/{len(ts_codes)} ({code})  ETA {eta:.0f}s  信号{len(all_signals)}只")
    
    # 四种形态都检测
    patterns_found = []
    for pattern_name, detect_fn in [
        ('强势横盘', detector.detect_sideways_pattern),
        ('深度回调', detector.detect_deep_pullback_pattern),
        ('放量回调', detector.detect_volume_pullback_pattern),
        ('V型急跌', detector.detect_vshape_pattern),
    ]:
        try:
            result = detect_fn(code, today_only=False)
            if result:
                patterns_found.append(result)
        except:
            pass
    
    # 同一只股票，按entry_date分组，每天只保留评分最高的
    by_date = {}
    for r in patterns_found:
        d = str(r.get('entry_date', ''))
        if d in ('20260624', '20260625', '20260626'):
            if d not in by_date or r['score'] > by_date[d]['score']:
                by_date[d] = r
    
    for d, r in by_date.items():
        c6 = code[:6]
        name = pool_df[pool_df['code'] == int(c6)]['name'].values[0] if len(pool_df[pool_df['code'] == int(c6)]) > 0 else code
        all_signals.append({
            'date': d,
            'code': code,
            'name': name,
            'pattern': r.get('pattern', ''),
            'score': r['score'],
            'pullback': r.get('pullback_pct', 0),
            'surge': r.get('wave1_gain', 0),
            'entry_price': r.get('entry_price', 0),
            'stop_loss': r.get('stop_loss', 0),
            'target': r.get('target', 0),
        })

elapsed = time.time() - t0
print(f"\n扫描完成！耗时 {elapsed:.0f}s，找到 {len(all_signals)} 只信号")

# 按日期分组输出
for target_date in ['20260624', '20260625', '20260626']:
    day_signals = [s for s in all_signals if s['date'] == target_date]
    day_signals = sorted(day_signals, key=lambda x: -x['score'])
    
    print(f"\n{'='*70}")
    print(f"{target_date}: {len(day_signals)} 只信号")
    print(f"{'='*70}")
    
    # 形态统计
    pattern_counts = {}
    for s in day_signals:
        p = s['pattern']
        pattern_counts[p] = pattern_counts.get(p, 0) + 1
    if pattern_counts:
        print(f"形态分布: {pattern_counts}")
    
    for i, s in enumerate(day_signals, 1):
        print(f"\n【第{i}名】{s['name']} ({s['code']})")
        print(f"  形态: {s['pattern']} | 评分: {s['score']}分")
        print(f"  入场价: {s['entry_price']:.2f} | 止损: {s['stop_loss']:.2f} | 目标: {s['target']:.2f}")
        print(f"  回调: {s['pullback']:.1f}% | 一波: {s['surge']:.1f}%")
    
    if not day_signals:
        print("  当天无信号")
    print()
