# -*- coding: utf-8 -*-
"""快速测试PDF生成（用少量股票）"""
import os, sys
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
from wave2_pattern_scanner import WavePatternDetector, generate_pdf_report

d = WavePatternDetector()

# 混合股票：主板+双创
test_codes = [
    '600183.SH', '603678.SH', '600699.SH',      # 主板
    '688519.SH', '688787.SH', '688981.SH',       # 双创
    '603929.SH', '002192.SZ', '301128.SZ',       # 混合
]

all_results = []
for code in test_codes:
    r1 = d.detect_sideways_pattern(code, today_only=False)
    if r1:
        all_results.append(r1)
    r2 = d.detect_deep_pullback_pattern(code, today_only=False)
    if r2:
        all_results.append(r2)

print(f'信号数: {len(all_results)}')
for r in all_results:
    print(f'  {r["ts_code"]} {r["pattern"]} score={r["score"]}')

if all_results:
    pdf = generate_pdf_report(all_results, len(test_codes), csv_name='test_pick')
    print(f'\nPDF: {pdf}')
