# -*- coding: utf-8 -*-
"""验证双创深度回调加分"""
import os, sys
sys.path.insert(0, r'D:\mystock')
if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break
from wave2_pattern_scanner import WavePatternDetector
d = WavePatternDetector()

# 找几只双创深度回调
test_codes = ['301128.SZ', '300750.SZ', '688981.SH']
for code in test_codes:
    r = d.detect_deep_pullback_pattern(code, today_only=False)
    if r:
        print(f'{code}: score={r["score"]} entry={r["entry_date"]}')
        # 检查是否有双创加分
        if '双创优选' in r['score_details']:
            print(f'  ✅ 双创深度回调加分生效')
        else:
            print(f'  评分细节: {r["score_details"]}')

# 也测一只主板深度回调看是否-3
test_main = ['603163.SH', '600699.SH']
for code in test_main:
    r = d.detect_deep_pullback_pattern(code, today_only=False)
    if r:
        print(f'{code}: score={r["score"]} entry={r["entry_date"]}')
        if '主板深度回调' in r['score_details']:
            print(f'  ✅ 主板深度回调压制生效')
