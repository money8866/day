# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
from wave2_pattern_scanner import WavePatternDetector
d = WavePatternDetector()

# 测试精选逻辑 - 603929是否被识别为主板
ts_code = '603929.SH'
is_main = ts_code.startswith(('600', '601', '603', '605', '000', '002'))
print(f'603929 is_main: {is_main}')

# 完整扫描少量股票验证精选
test_codes = ['600183.SH', '600301.SH', '603929.SH', '600601.SH', '603678.SH',
              '688543.SH', '301529.SZ', '301357.SZ']
all_results = []
for code in test_codes:
    r1 = d.detect_sideways_pattern(code, today_only=False)
    if r1:
        all_results.append(r1)
    r2 = d.detect_deep_pullback_pattern(code, today_only=False)
    if r2:
        all_results.append(r2)

# 模拟精选
main_sideways = [r for r in all_results
                 if r['pattern'] == '强势横盘' and r['ts_code'].startswith(('600', '601', '603', '605', '000', '002'))]
main_sideways = sorted(main_sideways, key=lambda x: x.get('score', 0), reverse=True)[:3]

print('\n主板强势横盘 TOP3:')
for i, r in enumerate(main_sideways, 1):
    print(f'  {i}. {r["ts_code"]} score={r["score"]}')
