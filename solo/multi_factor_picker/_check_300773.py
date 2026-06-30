# -*- coding: utf-8 -*-
"""深度分析300773拉卡拉：连涨7天为何还27分"""
import os, sys
sys.path.insert(0, r'D:\mystock')
if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break

import pandas as pd
import tushare as ts
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

code = '300773.SZ'
print(f'=== {code} 拉卡拉 详细分析 ===\n')

# 1. 获取180日日线
df = pro.daily(ts_code=code, start_date='20260101', end_date='20260623')
df = df.sort_values('trade_date').reset_index(drop=True)

# 最近30天明细
print('--- 最近30天行情 ---')
recent = df.tail(30)[['trade_date','open','high','low','close','pct_chg','vol']].to_string(index=False)
print(recent)

# 2. 识别一波高点
print(f'\n--- 一波拉升 ---')
high_idx = df['close'].idxmax()
# 一波高点在6/13之前
pre_crash = df[df['trade_date'] <= '20260613']
if len(pre_crash) > 0:
    wave1_high_idx = pre_crash['close'].idxmax()
    wave1_high = df.iloc[wave1_high_idx]
    # 找一波起点（向前找最低点）
    start_search = max(0, wave1_high_idx - 30)
    wave1_low_idx = df.iloc[start_search:wave1_high_idx+1]['close'].idxmin()
    wave1_low = df.iloc[wave1_low_idx]
    wave1_gain = (wave1_high['close'] / wave1_low['close'] - 1) * 100
    print(f'起点: {wave1_low["trade_date"]} 收盘{wave1_low["close"]:.2f}')
    print(f'高点: {wave1_high["trade_date"]} 收盘{wave1_high["close"]:.2f}')
    print(f'一波涨幅: +{wave1_gain:.1f}%')

# 3. 调整段
print(f'\n--- 调整段 ---')
post_high = df[df['trade_date'] > wave1_high['trade_date']]
if len(post_high) > 0:
    adjust_low = post_high['close'].min()
    adjust_low_date = post_high.loc[post_high['close'].idxmin(), 'trade_date']
    adjust_pct = (adjust_low / wave1_high['close'] - 1) * 100
    adjust_days = len(post_high[post_high['trade_date'] <= adjust_low_date])
    print(f'调整低点: {adjust_low_date} 收盘{adjust_low:.2f}')
    print(f'调整幅度: {adjust_pct:.1f}% ({adjust_days}天)')

# 4. 最近7天连涨
print(f'\n--- 近7天连涨 ---')
last7 = df.tail(7)[['trade_date','close','pct_chg']].copy()
last7['cum_gain'] = ((last7['close'] / last7['close'].iloc[0] - 1) * 100).round(2)
print(last7.to_string(index=False))
cum7 = (df.iloc[-1]['close'] / df.iloc[-7]['close'] - 1) * 100
print(f'7天累计涨幅: +{cum7:.1f}%')

# 5. 调整低点vs当前价
current = df.iloc[-1]['close']
from_low = (current / adjust_low - 1) * 100
print(f'\n调整低点{adjust_low:.2f} → 当前{current:.2f} = +{from_low:.1f}%')
print(f'距一波高点{wave1_high["close"]:.2f}: {(current/wave1_high["close"]-1)*100:.1f}%')

# 6. 评分时点的RSI（调整低点那天）
print(f'\n--- 评分时点分析 ---')
print(f'评分基于调整低点({adjust_low_date})，不是当前价！')
print(f'评分时RSI: 约16.5（极度超卖）')

# 7. 核心问题：连涨7天为何还入选？
print(f'\n=== 核心问题：连涨7天为何27分？ ===')
print(f'')
print(f'扫描器的工作方式：')
print(f'  1. 检测一波涨幅 ≥ 20%  → +22.6% ✓')
print(f'  2. 检测深度回调 ≥ 20%  → -36.3% ✓')
print(f'  3. 在调整低点评估共振评分 → 27分')
print(f'  4. --today 模式：要求 entry_date = 最近交易日')
print(f'')
print(f'问题出在 entry_date 判定！')
print(f'  调整最低点出现在哪天？')
# 找entry_date
from wave2_pattern_scanner import WavePatternDetector
detector = WavePatternDetector()
result = detector.detect_deep_pullback_pattern(code)
if result:
    print(f'  entry_date: {result["entry_date"]}')
    print(f'  entry_price: {result["entry_price"]}')
    print(f'  评分: {result["score"]}')
    print(f'  评分细节: {result["score_details"]}')
    
    # entry_date vs 今天
    today = '20260623'
    if result['entry_date'] == today:
        print(f'\n  ⚠️ entry_date=今天！但连涨7天的低点可能不在今天')
    else:
        print(f'\n  entry_date={result["entry_date"]} ≠ 今天{today}')
        print(f'  → 这不是"今日信号"，扫描器不该选它')

# 8. 检查stk_factor_pro的RSI
factor = pro.stk_factor_pro(ts_code=code, start_date='20260616', end_date='20260623')
if factor is not None and len(factor) > 0:
    factor = factor.sort_values('trade_date')
    print(f'\n--- stk_factor_pro 近期RSI ---')
    for _, row in factor.iterrows():
        rsi6 = row.get('rsi_bfq_6', 'N/A')
        rsi12 = row.get('rsi_bfq_12', 'N/A')
        print(f"  {row['trade_date']}  RSI6={rsi6}  RSI12={rsi12}")
