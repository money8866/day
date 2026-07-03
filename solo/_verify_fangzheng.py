# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

from multi_factor_picker import wave2_pattern_scanner as scanner
import numpy as np

detector = scanner.WavePatternDetector()

ts_code = '600601.SH'  # 方正科技

print("方正科技验证:")
print("=" * 70)

# 今日检测
result = detector.detect_sideways_pattern(ts_code, today_only=True)
print(f"\n今日检测: {'✅ 有信号' if result else '❌ 无信号'}")
if result:
    for k in ['score', 'wave1_gain', 'pullback_pct', 'adjust_days', 'entry_price', 'stop_loss', 'target']:
        print(f"    {k}: {result.get(k, 'N/A')}")

# 最近5天检测
print("\n最近5天信号:")
for i in range(0, 5):
    from datetime import datetime, timedelta
    d = datetime.now() - timedelta(days=i+1)
    date_str = d.strftime('%Y%m%d')
    r = detector.detect_sideways_pattern(ts_code, target_date=date_str)
    if r:
        print(f"  {date_str}: ✅ 评分{r['score']} 入场{r['entry_price']}")
    else:
        print(f"  {date_str}: ❌ 无信号")

# 查看最近30日K线
df = detector.load_data(ts_code, lookback=500)
if df is not None:
    print(f"\n最近30日K线:")
    print(f"{'日期':<10} {'收盘':<8} {'涨幅%':<6} {'MA20':<8} {'距MA20%':<8}")
    for _, r in df.tail(30).iterrows():
        ma20 = r.get('ma_bfq_20', 0) or r.get('ma20', 0)
        pct = (r['close']/ma20-1)*100 if ma20>0 else 0
        print(f"{r['trade_date']:<10} {r['close']:<8.2f} {r.get('pct_chg',0):<6.2f} {ma20:<8.2f} {pct:<+8.2f}%")

    # 分析波峰候选
    closes = df['close'].values
    n = len(df)
    candidates = detector._find_recent_wave1(closes, n, max_lookback=80)
    print(f"\n波峰候选（max_lookback=80）:")
    for i, (h, l, sg) in enumerate(candidates):
        print(f"  候选{i+1}: {df.iloc[h]['trade_date']} 价={closes[h]:.2f} 涨幅={sg*100:.1f}%")
