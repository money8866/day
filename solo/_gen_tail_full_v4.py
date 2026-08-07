# -*- coding: utf-8 -*-
"""「猎尾V4」中报池回踩扫描单测: 模拟 9 只精选股 14:50 实时行情, 验证二次确认评分"""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from realtime_theme_monitor import RealtimeThemeMonitor

# 绕过 __init__ (不连TDX/不加载主题)
m = RealtimeThemeMonitor.__new__(RealtimeThemeMonitor)
m.quotes = {}
m.intraday_snapshots = {}
m.tail_entry_debug_printed = False

# 读取精选池(与 scan 内部逻辑一致的精选条件)
df = pd.read_csv('report_daily/enhanced_timing_bull_all_20260806.csv', encoding='utf-8-sig')
for c in ('洗盘修复分', '量化择时分', 'VWAP', 'MA20', '现价', 'ATR动态止损价', 'ATR跟踪止盈价'):
    df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
elite = df[
    df['修正后胜率分级'].isin(['S', 'A']) &
    (df['洗盘修复分'] >= 80) &
    df['兑现冲击过滤'].astype(str).str.contains('✅', na=False) &
    df['回踩确认'].astype(str).str.contains('✅', na=False)
].reset_index(drop=True)
print(f"精选池: {len(elite)}只")

# 上证指数(弱市模拟用 +0.3%)
m.quotes['000001.SH'] = {'price': 3300, 'pct_chg': 0.3}

# 模拟 9 只精选股实时行情: 场景分别覆盖 回踩到位/企稳/微涨反抽/追高/回踩过深/破位
scenarios = [
    ('000975.SZ', -1.8, 0.10, '缩量回踩'),   # 山金国际: 回踩到位+缩量
    ('600988.SH', -0.6, 0.08, '回踩企稳'),   # 赤峰黄金
    ('002493.SZ', +0.4, 0.12, '微涨企稳'),   # 荣盛石化
    ('688046.SH', +2.8, 0.20, '反抽偏快'),   # 药康生物
    ('603110.SH', +8.2, 0.10, '追高剔除'),   # 东方材料 +8.2%
    ('600801.SH', -5.5, 0.15, '回踩过深剔除'),# 华新建材 -5.5%
    ('603819.SH', -1.2, 0.45, '尾盘放量'),   # 神力股份 放量
    ('600497.SH', -2.5, 0.60, '回踩偏深'),   # 驰宏锌锗 -2.5
    ('600230.SH', +1.5, 0.09, '缩量微涨'),   # 沧州大化
]
for code, ret, tail_add, label in scenarios:
    row = elite[elite['代码'].astype(str).str.strip() == code]
    if row.empty:
        print(f"  !! {code} 不在精选池")
        continue
    last = float(row.iloc[0]['现价'])
    price = last * (1 + ret / 100)
    prev = last * 1.0  # 昨收=CSV现价
    m.quotes[code] = {
        'price': round(price, 2),
        'pct_chg': round((price / prev - 1) * 100, 2),
        'amount': 5e8, 'vol': 2000000,
        'last_close': round(prev, 2),
        'open': round(prev * (1 + ret / 200), 2),
        'high': round(price * 1.01, 2),
        'low': round(price * 0.99, 2),
    }
    # 快照: 模拟尾盘增量占比 tail_add
    noon_vol = 2000000
    tail_base_vol = int(noon_vol * (1 + tail_add))
    m.intraday_snapshots[code] = {
        'morning_vol': 1200000,
        'noon_vol': noon_vol,
        'tail_base_vol': tail_base_vol,
    }

signals = m.scan_tail_recovery_v3()
print(f"\n== 信号数: {len(signals)} ==")
for s in signals:
    d = s['detail']
    print(f"{s['ts_code']} {s['name']:<6} 总分{s['total_score']} 量化{d['quant_score']:.1f} 二次{d['realtime_score']} "
          f"回踩{d['ret_pct']:+.1f}% 乖MA20{d['rise_gap_ma20']:+.1f}% 乖VWAP{d['rise_gap_vwap']:+.1f}% "
          f"空间{d['upside_pct']:+.1f}% {d['tail_vol_label']} [{d['buy_point']}] {s['signal']}")

# 断言: 追高(+8.2%)与回踩过深(-5.5%)必须被剔除
codes = [s['ts_code'] for s in signals]
assert '603110.SH' not in codes, "东方材料追高应被剔除!"
assert '600801.SH' not in codes, "华新建材回踩过深应被剔除!"
print("\n✅ 断言通过: 追高/回踩过深已正确剔除")
