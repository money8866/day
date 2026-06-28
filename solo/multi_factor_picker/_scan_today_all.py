# -*- coding: utf-8 -*-
"""
用scan_pool扫描全池，看四种形态结果
"""
import sys, os
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import pandas as pd
import wave2_pattern_scanner as scanner

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

detector = scanner.WavePatternDetector()
df = detector.scan_pool(ts_codes, pattern='all', pool_name='合格池', today_only=True)

if df is not None and len(df) > 0:
    # 按评分排序
    df = df.sort_values('score', ascending=False)
    
    print(f"\n{'='*70}")
    print(f"今日(20260626)四种形态信号汇总")
    print(f"{'='*70}")
    print(f"总数: {len(df)} 只")
    
    # 按形态统计
    if 'pattern' in df.columns:
        print(f"\n形态分布:")
        for p, cnt in df['pattern'].value_counts().items():
            print(f"  {p}: {cnt} 只")
    
    print(f"\n{'='*70}")
    print("详细信号列表:")
    print(f"{'='*70}")
    
    for i, row in df.iterrows():
        name = row.get('name', '')
        code = row.get('ts_code', '')
        pattern = row.get('pattern', '')
        score = row.get('score', 0)
        pullback = row.get('pullback_pct', 0)
        surge = row.get('wave1_gain', 0)
        entry = row.get('entry_price', 0)
        stop = row.get('stop_loss', 0)
        target = row.get('target', 0)
        
        print(f"\n【{pattern}】{name} ({code})")
        print(f"  评分: {score}分 | 入场: {entry:.2f} | 止损: {stop:.2f} | 目标: {target:.2f}")
        print(f"  回调: {pullback:.1f}% | 一波: {surge:.1f}%")
else:
    print("\n今日无信号")
