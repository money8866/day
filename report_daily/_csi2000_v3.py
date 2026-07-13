# -*- coding: utf-8 -*-
import sys, datetime
from pytdx.hq import TdxHq_API

sys.stdout.reconfigure(encoding='utf-8')
api = TdxHq_API(heartbeat=False)
api.connect('123.125.108.14', 7709)

# 中证2000: 932000 (沪), 国证2000: 399303 (深), 中证1000: 931025 (沪)
# 实际用国证2000(399303)作为中证2000的近似
test_codes = [
    (1, '932000', '中证2000'),
    (0, '932000', '中证2000深'),
    (0, '399303', '国证2000'),
    (1, '931025', '中证1000'),
    (0, '399852', '中证2000深2'),
]

all_data = None
label = ''
for market, code, name in test_codes:
    data = api.get_security_bars(4, market, code, 0, 300)
    if data and len(data) > 100:
        # 过滤掉异常数据
        valid = [d for d in data if 1000 < d['close'] < 100000 and 1000 < d['high'] < 100000
                 and d['year'] >= 2024 and d['year'] <= 2027]
        if len(valid) > 100:
            all_data = valid
            label = name
            print(f"✅ 找到 {name}: market={market}, code={code}, {len(valid)}条有效数据")
            break

api.disconnect()

if not all_data:
    print("❌ 无法获取有效数据")
    sys.exit(1)

# 解析 + 按日期排序
rows = []
for d in all_data:
    try:
        date = datetime.date(d['year'], d['month'], d['day'])
        rows.append({
            'date': date,
            'open': d['open'],
            'high': d['high'],
            'low': d['low'],
            'close': d['close'],
            'vol': d['vol']
        })
    except:
        continue

# 按日期升序排列
rows.sort(key=lambda x: x['date'])
print(f"有效数据: {len(rows)} 条")
print(f"区间: {rows[0]['date']} ~ {rows[-1]['date']}")

# ── ABC波浪分析 ──
# 1. 找全局最高点(A浪起点)
peak_idx = max(range(len(rows)), key=lambda i: rows[i]['high'])
peak = rows[peak_idx]
print(f"\n{'='*50}")
print(f"📍 A浪起点(最高点): {peak['date']}  高={peak['high']:.2f}  收={peak['close']:.2f}")

# 2. 从最高点往后找最低点(A浪终点)
post_peak = rows[peak_idx:]
a_end_idx = peak_idx + min(range(len(post_peak)), key=lambda i: post_peak[i]['low'])
a_end = rows[a_end_idx]
a_drop = (peak['high'] - a_end['low']) / peak['high'] * 100
a_drop_abs = peak['high'] - a_end['low']
print(f"📍 A浪终点(最低点): {a_end['date']}  低={a_end['low']:.2f}  跌={a_drop:.2f}%")

# 3. 找A浪内部的反弹次高点(小B浪)
sub_b_idx = None
sub_b_high = 0
for i in range(peak_idx+1, a_end_idx):
    h = rows[i]['high']
    if h > sub_b_high:
        sub_b_high = h
        sub_b_idx = i
sub_b = rows[sub_b_idx] if sub_b_idx else None
if sub_b:
    sub_b_drop = (sub_b['high'] - a_end['low']) / sub_b['high'] * 100
    print(f"📍 A浪次高点(小B): {sub_b['date']}  高={sub_b['high']:.2f}  距高={sub_b_drop:.2f}%")

# 4. C浪起点 = A浪低点后的反弹高点
post_a = rows[a_end_idx:]
c_start_idx = None
c_start_price = 0
for i in range(1, min(len(post_a), 30)):  # 反弹不超过30天
    h = post_a[i]['high']
    if h > c_start_price and h > a_end['low'] * 1.015:
        c_start_price = h
        c_start_idx = a_end_idx + i

if c_start_idx:
    c_start = rows[c_start_idx]
    c_drop_abs = c_start['high'] - a_end['low']
    print(f"📍 C浪起点: {c_start['date']}  高={c_start['high']:.2f}")
    print(f"    (A浪终点到C浪起点反弹: +{c_drop_abs:.2f}, +{c_drop_abs/a_end['low']*100:.2f}%)")
else:
    # 还在A浪中，用当前价
    c_start_price = rows[-1]['close']
    c_start_idx = len(rows) - 1
    print(f"📍 C浪起点(当前): {rows[-1]['date']}  价={c_start_price:.2f} (尚未明显反弹)")

# 5. 当前状态
current = rows[-1]
print(f"\n📍 当前: {current['date']}  收={current['close']:.2f}  低={current['low']:.2f}")

# C浪从起点跌幅
c_from_start = (c_start_price - current['close']) / c_start_price * 100
print(f"    C浪已从起点跌: {c_from_start:.2f}%")

# ── C浪目标预测 ──
print(f"\n{'='*50}")
print(f"📊 C浪目标位预测")
print(f"{'='*50}")
print(f"  A浪起点: {peak['high']:.2f}")
print(f"  A浪终点: {a_end['low']:.2f}")
print(f"  A浪跌幅: -{a_drop:.2f}%")
print(f"  C浪起点: {c_start_price:.2f}")
print(f"  当前价:  {current['close']:.2f}")
print()

# C浪=A浪的各比例目标
a_total = peak['high'] - a_end['low']
print("  斐波那契比例预测:")
for ratio, name in [(0.618, '0.618(61.8%)'), (0.786, '0.786(78.6%)'),
                    (1.0, '1.000(等长)'), (1.236, '1.236'), (1.382, '1.382(1.272)'),
                    (1.618, '1.618(延展)')]:
    target = c_start_price - a_total * ratio
    diff = target - current['close']
    pct_diff = diff / current['close'] * 100
    mark = ""
    if ratio == 1.0: mark = " ← 经典等长"
    elif ratio == 0.618: mark = " ← 乐观(最小延展)"
    elif ratio == 1.382: mark = " ← 悲观"
    elif ratio == 1.618: mark = " ← 极端"
    print(f"  C=A×{ratio:.3f}: 目标={target:.2f}  距当前{pct_diff:+.1f}%{mark}")

print(f"\n  A浪低点:  {a_end['low']:.2f}  ← 参考支撑")
print(f"  历史支撑: {a_end['low']*0.95:.2f}  ← A浪低点×0.95")

# ── 近期K线 ──
print(f"\n{'='*50}")
print(f"近30日K线:")
print(f"  {'日期':10} {'开盘':8} {'最高':8} {'最低':8} {'收盘':8} {'涨跌幅':8}")
prev_c = None
for row in rows[-30:]:
    pct = (row['close']-prev_c)/prev_c*100 if prev_c else 0
    flag = "🔴" if pct > 0 else "🟢"
    mark = " ← A浪终点" if row['date'] == a_end['date'] else (" ← C浪起点" if row['date'] == rows[c_start_idx]['date'] else "")
    print(f"  {str(row['date']):10} {row['open']:8.2f} {row['high']:8.2f} {row['low']:8.2f} {row['close']:8.2f} {pct:+7.2f}% {flag}{mark}")
    prev_c = row['close']

print(f"\n✅ 完成")
