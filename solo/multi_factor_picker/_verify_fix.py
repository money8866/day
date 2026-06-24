# -*- coding: utf-8 -*-
"""验证除权修复后扫描器的正确性"""
import os, sys
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
from wave2_pattern_scanner import WavePatternDetector

d = WavePatternDetector()

# 测试几只股票
test_codes = ['603929.SH', '688787.SH', '600183.SH']
for code in test_codes:
    r1 = d.detect_sideways_pattern(code, today_only=True)
    r2 = d.detect_deep_pullback_pattern(code, today_only=True)
    
    name = ''
    df = d.load_data(code, lookback=180)
    if df is not None and len(df) > 0:
        # 检查close vs close_bfq
        last = df.iloc[-1]
        cb = last.get('close_bfq', 'N/A')
        cq = last.get('close_qfq', last['close'])
        print(f'{code}: close(qfq)={last["close"]:.2f} close_bfq={cb} close_qfq_raw={cq:.4f}')

    if r1:
        print(f'  强势横盘: score={r1["score"]} entry={r1["entry_price"]}')
    if r2:
        print(f'  深度回调: score={r2["score"]} entry={r2["entry_price"]}')
    if not r1 and not r2:
        print(f'  无信号')

# 关键：300773在today模式下不应入选
print('\n=== 300773 除权验证 ===')
r = d.detect_deep_pullback_pattern('300773.SZ', today_only=True)
if r:
    print(f'❌ BUG: 300773仍被选中! score={r["score"]}')
else:
    print('✅ 正确: 300773在today模式下被过滤')
