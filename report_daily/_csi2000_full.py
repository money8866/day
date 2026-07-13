# -*- coding: utf-8 -*-
import os, sys, datetime
import pandas as pd
import numpy as np
import tushare as ts

sys.stdout.reconfigure(encoding='utf-8')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
pro = ts.pro_api('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

today = datetime.date.today().strftime('%Y%m%d')

# 获取中证2000全部历史数据
print("获取中证2000全部数据...")
df = pro.index_daily(ts_code='932000.CSI', start_date='20230101', end_date=today)
df = df.sort_values('trade_date').reset_index(drop=True)
print(f"总数据: {len(df)} 条, {df.iloc[0]['trade_date']} ~ {df.iloc[-1]['trade_date']}")

# 转日期
df['date'] = pd.to_datetime(df['trade_date'])
df['close'] = df['close'].astype(float)
df['high'] = df['high'].astype(float)
df['low'] = df['low'].astype(float)
df['open'] = df['open'].astype(float)

# ── ABC波浪分析 ──
# 找全局最高点(A浪起点)
peak_idx = df['high'].idxmax()
peak = df.loc[peak_idx]
peak_price = peak['high']
peak_date = peak['trade_date']
print(f"\n{'='*55}")
print(f"📍 A浪起点(最高点): {peak_date}  高={peak_price:.2f}  收={peak['close']:.2f}")

# 最高点之后的数据
post_peak = df[df.index > peak_idx].copy()
if len(post_peak) == 0:
    print("❌ 最高点之后无数据(当前可能就是最高点)")
    sys.exit(1)

# A浪终点 = 最高点之后的最低点
a_end_idx = post_peak['low'].idxmin()
a_end = df.loc[a_end_idx]
a_end_price = a_end['low']
a_end_date = a_end['trade_date']
a_drop_pct = (peak_price - a_end_price) / peak_price * 100
a_drop_abs = peak_price - a_end_price
print(f"📍 A浪终点(最低点): {a_end_date}  低={a_end_price:.2f}  跌幅=-{a_drop_pct:.2f}%")

# A浪内部次高点(小B浪反弹)
post_peak_before_min = df[(df.index > peak_idx) & (df.index < a_end_idx)].copy()
sub_b_idx = post_peak_before_min['high'].idxmax() if len(post_peak_before_min) > 0 else None
sub_b_price = 0
sub_b_date = ''
sub_b_drop_pct = 0
if sub_b_idx is not None:
    sub_b = df.loc[sub_b_idx]
    sub_b_price = sub_b['high']
    sub_b_date = sub_b['trade_date']
    sub_b_drop_pct = (sub_b_price - a_end_price) / sub_b_price * 100
    print(f"📍 A浪次高点(小B): {sub_b_date}  高={sub_b_price:.2f}  回调-{sub_b_drop_pct:.2f}%")

# C浪起点 = A浪终点后的反弹高点
post_a = df[df.index > a_end_idx].copy()
c_start_idx = None
c_start_price = 0
c_start_date = ''
for i, (idx, row) in enumerate(post_a.iterrows()):
    if i == 0:
        continue
    if row['high'] > a_end_price * 1.02:  # 反弹超2%
        c_start_idx = idx
        c_start_price = row['high']
        c_start_date = row['trade_date']
        break

if c_start_idx is None:
    # 尚未明显反弹，用当前价
    c_start_idx = df.index[-1]
    c_start_price = df.iloc[-1]['close']
    c_start_date = df.iloc[-1]['trade_date']
    print(f"\n📍 C浪起点(当前/未反弹): {c_start_date}  高={c_start_price:.2f}")
else:
    c_rebound = (c_start_price - a_end_price) / a_end_price * 100
    print(f"\n📍 C浪起点(反弹高点): {c_start_date}  高={c_start_price:.2f}  反弹+{c_rebound:.2f}%")

# 当前状态
current = df.iloc[-1]
current_date = current['trade_date']
current_close = current['close']
current_low = current['low']
print(f"\n📍 当前: {current_date}  收={current_close:.2f}  当日低={current_low:.2f}")

# C浪从起点跌幅
c_fall_from_start = (c_start_price - current_close) / c_start_price * 100
c_fall_from_peak = (peak_price - current_close) / peak_price * 100
print(f"    C浪已从起点跌: -{c_fall_from_start:.2f}%")
print(f"    从最高点总跌幅: -{c_fall_from_peak:.2f}%")

# ── C浪目标预测 ──
a_total = peak_price - a_end_price  # A浪总跌幅(绝对值)
print(f"\n{'='*55}")
print(f"📊 C浪目标位预测")
print(f"{'='*55}")
print(f"  A浪起点(最高): {peak_price:.2f}")
print(f"  A浪终点(最低): {a_end_price:.2f}")
print(f"  A浪跌幅: -{a_drop_pct:.2f}% (绝对值: {a_total:.2f}点)")
print(f"  C浪起点:       {c_start_price:.2f}")
print(f"  当前收盘:       {current_close:.2f}")
print()
print(f"  {'比例':10} {'目标位':10} {'距当前':10} {'说明'}")
print(f"  {'-'*50}")

targets = [
    (0.618, '乐观最小'),
    (0.786, '保守'),
    (1.000, '经典等长'),
    (1.236, '延展1.236'),
    (1.272, '延伸1.272'),
    (1.382, '悲观延展'),
    (1.500, '1.5倍'),
    (1.618, '极端延展'),
]

for ratio, label in targets:
    target = c_start_price - a_total * ratio
    dist = (target - current_close) / current_close * 100
    dist_str = f"{dist:+.1f}%"
    if dist > 0:
        dist_str = f"{dist:+.1f}%"
    else:
        dist_str = f"{dist:.1f}%"
    
    marks = []
    if ratio == 1.0: marks.append("← 经典")
    elif ratio == 0.618: marks.append("← 乐观")
    elif ratio == 1.382: marks.append("← 悲观")
    elif ratio == 1.618: marks.append("← 极端")
    elif ratio == 0.786: marks.append("← 61.8%回撤")
    
    mark = ''.join(marks)
    
    # 判断是否已破
    if current_close <= target:
        status = "⚠️ 已破"
    elif current_low <= target:
        status = "⚠️ 当日已触及"
    else:
        status = ""
    
    print(f"  {ratio:.3f}      {target:8.2f}   {dist_str:8}   {label} {mark} {status}")

print()
print(f"  {'A浪低点':10} {a_end_price:8.2f}   {'--':8}   ← 参考支撑")
print(f"  {'-5%':10} {a_end_price*0.95:8.2f}   {'--':8}   ← A浪低点×0.95")

# ── 斐波那契回撤位 ──
print(f"\n{'='*55}")
print(f"📐 斐波那契回撤位(从最高点)")
print(f"{'='*55}")
print(f"  {'比例':10} {'回撤位':10} {'距当前':10} {'说明'}")
fib_levels = [
    (0.236, '23.6%'),
    (0.382, '38.2%'),
    (0.500, '50.0%'),
    (0.618, '61.8%'),
    (0.786, '78.6%'),
]
for ratio, name in fib_levels:
    level = peak_price - a_total * ratio
    dist = (level - current_close) / current_close * 100
    print(f"  {name:8} {level:8.2f}   {dist:+.1f}%")

# ── 近期K线 ──
print(f"\n{'='*55}")
print(f"近40日K线:")
print(f"{'='*55}")
print(f"  {'日期':10} {'开盘':8} {'最高':8} {'最低':8} {'收盘':8} {'涨跌':8}")
recent = df.tail(40).copy()
prev_c = None
for _, row in recent.iterrows():
    pct = (row['close'] - prev_c) / prev_c * 100 if prev_c else 0
    flag = "🔴" if pct > 0 else "🟢"
    mark = ""
    if row['trade_date'] == peak_date: mark = " ← A起点"
    elif row['trade_date'] == a_end_date: mark = " ← A终点"
    elif row['trade_date'] == c_start_date: mark = " ← C起点"
    print(f"  {row['trade_date']:10} {row['open']:8.2f} {row['high']:8.2f} {row['low']:8.2f} {row['close']:8.2f} {pct:+7.2f}% {flag}{mark}")
    prev_c = row['close']

print(f"\n✅ 分析完成")
